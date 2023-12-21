import argparse
import asyncio
import os
import shutil
import subprocess
from zipfile import ZipFile
import psutil
import json
import time


import torch
from cog import BaseModel, Input, Path

from config import (
    LOCAL_TRAINING_WEIGHTS_PATH,
    REMOTE_TRAINING_WEIGHTS_PATH,
    REMOTE_TRAINING_FILES_TO_DOWNLOAD,
    MODEL_NAME,
)

from src.utils import maybe_download_with_pget, download_file_with_pget


MODEL_OUT = "/src/tuned_weights.tensors"
CHECKPOINT_DIR = "checkpoints"
SAVE_STRATEGY = "epoch"
OUTPUT_DIR = "/src/training_output"


class TrainingOutput(BaseModel):
    weights: Path


def remap_train_data(train_data):
    """Quick and hacky"""
    train_dir = f"/tmp/{int(time.time())}remapped_train_data.jsonl"
    with open(train_data, "r") as f:
        lines = [json.loads(val) for val in f]

    new_lines = [{"text": val["prompt"] + "\n" + val["completion"]} for val in lines]
    with open(train_dir, "w") as f:
        for line in new_lines:
            f.write(json.dumps(line))
            f.write("\n")
    return train_dir


def train(
    fake_output: str = Input(description="fake training", default=None),
    train_data: Path = Input(
        description="path to data file to use for fine-tuning your model"
    ),
    num_train_epochs: int = Input(
        description="number of training epochs",
        ge=1,
        default=1,
    ),
    train_batch_size: int = Input(
        description="Global batch size. This specifies the batch size that will be used to calculate gradients.",
        default=4,
        ge=1,
    ),
    gradient_accumulation_steps: int = Input(
        description="Number of training steps (each of train_batch_size) to update gradients for before performing a backward pass.",
        default=1,
        ge=1,
    ),
    # num_validation_samples: int = Input(
    #     description=(
    #         "Number of samples to use for validation."
    #         "If `run_validation` is `True` and `validation_data` is not specified, this number of samples"
    #         "will be selected from the tail of the training data. If `validation_data` is specified, this"
    #         "number of samples will be selected from the head of the validation data, up to the size of the validation data."
    #     ),
    #     default=50,
    #     ge=1,
    # ),
    # validation_data: Path = Input(
    #     description="path to optional evaluation data file to use for model eval",
    #     default=None,
    # ),
    # validation_batch_size: int = Input(
    #     description="Batch size for evaluation", default=1, ge=1
    # ),
    # run_validation: bool = Input(
    #     description="Whether to run validation during training.", default=True
    # ),
    # validation_prompt: str = Input(
    #     description="Prompt to use for generation during validation. If provided, a response to this prompt will be sampled and logged during validation.",
    #     default=None,
    # ),
    validation_frac: float = Input(
        description=("Percentage of training dataset to use for validation"),
        default=0.03,
        ge=0,
        le=1,
    ),
    learning_rate: float = Input(
        description="learning rate, for learning!", default=0.0002, ge=0
    ),
    pack_sequences: bool = Input(
        description="If 'True', sequences will be packed into a single sequences up to a given length. This improves computational efficiency.",
        default=False,
    ),
    # wrap_packed_sequences: bool = Input(
    #     description="If 'pack_sequences' is 'True', this will wrap packed sequences across examples, ensuring a constant sequence length but breaking prompt formatting.",
    #     default=False,
    # ),
    # chunk_size: int = Input(
    #     description="If 'pack_sequences' is 'True', this will chunk sequences into chunks of this size.",
    #     default=2048,
    #     ge=1,
    # ),
    peft_method: str = Input(
        description="Training method to use. Currently, 'lora' and 'qlora'.",
        default="lora",
        choices=["lora", "qlora"],
    ),
    seed: int = Input(description="random seed to use for training", default=42),
    local_model_path: str = Input(
        description="Path to local model to use for training. If not specified, will download a model based on `REMOTE_TRAINING_WEIGHTS_PATH`.",
        default=None,
    ),
    # weights: Path = Input(
    #     description="location of weights that are going to be fine-tuned", default=None
    # ),
    #
    # warmup_ratio: float = Input(
    #     description="pct of steps for a linear learning rate warmup",
    #     ge=0,
    #     le=0.5,
    #     default=0.03,
    # ),
    # max_steps: int = Input(
    #     description="number of steps to run training for, supersedes num_train_epochs",
    #     default=-1,
    # ),
    # logging_steps: int = Input(
    #     description="number of steps between logging epoch & loss", default=1
    # ),
    lora_rank: int = Input(description="Rank of the lora matrices", default=8, ge=1),
    lora_alpha: int = Input(
        description="Alpha parameter for scaling lora weights; weights are scaled by alpha/rank",
        default=16,
        ge=1,
    ),
    lora_dropout: float = Input(
        description="Dropout for lora training", default=0.05, ge=0.0, le=1.0
    ),
    # lora_target_modules: str = Input(description="Comma-separated list of lora modules to target, i.e. 'q_proj,v_proj'. Leave blank for default.", default="q_proj,v_proj")
) -> TrainingOutput:
    if fake_output:
        out_path = f"/tmp/{os.path.basename(fake_output)}"
        asyncio.run(download_file_with_pget(fake_output, out_path))
        return TrainingOutput(weights=Path(out_path))

    # Hardcode QLoRA for 70B models for now
    if "70" in MODEL_NAME and peft_method != "qlora":
        print("Using 70B model, setting peft_method to qlora")
        peft_method = "qlora"

    if not local_model_path:
        weights = REMOTE_TRAINING_WEIGHTS_PATH

        if "http" in weights:
            print(f"Downloading weights to {LOCAL_TRAINING_WEIGHTS_PATH}...")
            model_path = maybe_download_with_pget(
                LOCAL_TRAINING_WEIGHTS_PATH,
                weights,
                REMOTE_TRAINING_FILES_TO_DOWNLOAD,
            )

    else:
        model_path = local_model_path

    model_path = os.path.abspath(model_path)

    root_path = os.getcwd()

    output_dir = OUTPUT_DIR
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    num_gpus = torch.cuda.device_count()

    print(f"Local Output Dir: {output_dir}")
    print(f"Number of GPUs: {num_gpus}")

    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["HF_DATASETS_CACHE"] = "/src/.hf-cache"

    # hack to quickly handle prompt: completion: dataset
    with open(train_data, "r") as f:
        line_zero = f.readline()
    jl = json.loads(line_zero)
    if "prompt" in jl.keys():
        train_data = remap_train_data(train_data)

    args = ["accelerate", "launch", "-m", "axolotl.cli.train"]

    # pull base config - right now we'll hard code this to mistral, easy enough to map
    base_config = "/src/train_config_axolotl.yaml"
    config_to_run = "/src/config-to-run.yaml"

    # dataset workaround. we need to write dataset to a new file every time, and can't override on the cli
    shutil.copy(base_config, config_to_run)

    args.append(config_to_run)

    dataset_string = f"\ndatasets:\n  - path: {train_data}\n    type: completion"

    with open(config_to_run, "a") as f:
        f.write(dataset_string)

    args.extend(
        [
            f"--base_model={model_path}",
            f"--output_dir={output_dir}",
            # Preprocessing arguments
            f"--sample_packing={pack_sequences}",
            f"--pad_to_sequence_len={pack_sequences}",
            # Train arguments
            f"--num_epochs={num_train_epochs}",
            f"--micro_batch_size={train_batch_size}",
            f"--gradient_accumulation_steps={gradient_accumulation_steps}",
            f"--learning_rate={learning_rate}",
            f"--lora_r={lora_rank}",
            f"--lora_alpha={lora_alpha}",
            f"--lora_dropout={lora_dropout}",
            # Validation arguments
            f"--val_set_size={validation_frac}",
            # Other arguments
            f"--seed={seed}",
        ]
    )

    print(f"Train.py Arguments: \n{args}")

    p = None
    try:
        p = subprocess.Popen(args, close_fds=False, cwd="./axolotl/src")
        p.wait()
        return_code = p.poll()
        if return_code != 0:
            raise Exception(
                f"Training failed with exit code {return_code}! Check logs for details"
            )
        out_path = "training_output.zip"

        directory = Path(output_dir)
        with ZipFile(out_path, "w") as zip:
            for file_name in ["adapter_model.bin", "adapter_config.json"]:
                file_path = os.path.join(directory, file_name)
                print(file_path)
                zip.write(file_path, arcname=file_name)

        return TrainingOutput(weights=Path(out_path))
    finally:
        if p and p.poll() is None:
            top = psutil.Process(p.pid)
            children = top.children(recursive=True)
            for process in children + [top]:
                process.terminate()
            _, alive = psutil.wait_procs(children + [top], timeout=5)
            if alive:
                for process in alive:
                    print(f"process {process.pid} survived termination")
            else:
                print("terminated all processes successfully")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fine-tune a language model on a text dataset"
    )
    parser.add_argument(
        "--train_data", type=Path, required=True, help="Path to the json dataset"
    )
    parser.add_argument(
        "--eval_data",
        type=Path,
        required=False,
        help="Path to the json dataset",
        default=None,
    )
    parser.add_argument(
        "--weights",
        type=str,
        default=None,
        help="The model class to fine-tune on HF or as a local path (e.g. 'google/flan-t5-xxl'",
    )
    parser.add_argument(
        "--num_train_epochs", type=int, required=True, help="Number of training epochs"
    )
    parser.add_argument(
        "--learning_rate",
        type=float,
        default=2e-5,
        help="Learning rate for the optimizer",
    )
    parser.add_argument(
        "--train_batch_size", type=int, default=4, help="Batch size for training"
    )
    parser.add_argument(
        "--warmup_ratio",
        type=float,
        default=0.03,
        help="Number of warmup steps for the learning rate scheduler",
    )
    parser.add_argument(
        "--max_steps",
        type=int,
        default=0,
        help="Number of training steps to run, overrides num_train_epochs, useful for testing",
    )
    parser.add_argument(
        "--gradient_accumulation_steps",
        type=int,
        default=8,
        help="Number of training steps to run, overrides num_train_epochs, useful for testing",
    )
    parser.add_argument("--logging_steps", type=int, default=1)
    some_args = parser.parse_args()
    train(**vars(some_args))