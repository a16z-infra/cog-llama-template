import os
import subprocess
import torch

from collections import OrderedDict
from dotenv import load_dotenv
from transformers import LlamaTokenizer, AutoConfig, LlamaForCausalLM

from src.utils import get_env_var_or_default


load_dotenv()

MODEL_NAME = "llama-2-7b"
# INFERENCE CONFIGURATION
#######################################################################
# --------------------Notes--------------------------------------------
# We sometimes implement inference differently for models that have not 
# been trained/fine-tuned vs. those that have been trained/fine-tuned. We refer to the 
# former as "default" and the latter as "trained". Below, you can
# set your "default inference configuration" and your "trained
# inference configuration". 
#
# GENERAL INFERENCE CONFIGURATION
# -------------------------------
# This section defines the general inference configuration,
# which is used for both trained and untrained models.
# -------------------------------

LOAD_IN_4BIT = False
TOKENIZER_PATH = f"models/{MODEL_NAME}/model_artifacts/default_inference_weights"
USE_SYSTEM_PROMPT = False
USE_EXLLAMA_FOR_UNTRAINED_WEIGHTS = False


# DEFAULT INFERENCE CONFIGURATION
# -------------------------------
# This section defines the default inference configuration, which may differ from
# how we implement inference for a trained model.
# -------------------------------


LOCAL_DEFAULT_INFERENCE_WEIGHTS_PATH = f"models/{MODEL_NAME}/model_artifacts/default_inference_weights"

REMOTE_DEFAULT_INFERENCE_WEIGHTS_PATH = "https://99cbf3a41e6555b9823d472b00025e1c.r2.cloudflarestorage.com/replicate-llama-hosting/Llama-2-7b/"

N_SHARDS = 3
REMOTE_TRAINING_FILES_TO_DOWNLOAD = [
    f"model-{str(i+1).zfill(5)}-of-{str(N_SHARDS).zfill(5)}.safetensors"
    for i in range(N_SHARDS)
]

# REMOTE_DEFAULT_INFERENCE_FILES_TO_DOWNLOAD = ["model.safetensors"]
# N_SHARDS=3
# REMOTE_DEFAULT_INFERENCE_FILES_TO_DOWNLOAD = [
    # f"pytorch_model-{str(i+1).zfill(5)}-of-{str(N_SHARDS).zfill(5)}.bin"
    # for i in range(N_SHARDS)
# ]

REMOTE_DEFAULT_INFERENCE_FILES_TO_DOWNLOAD += [
    "checklist.chk",
    "config.json",
    "model.safetensors.index.json",
    "params.json",
    "tokenizer.model",
    "tokenizer_checklist.chk",
]

# TRAINED INFERENCE CONFIGURATION
# -------------------------------
# This section defines the inference configuration for fine-tuned models
# -------------------------------

LOCAL_TRAINING_WEIGHTS_PATH = f"models/{MODEL_NAME}/model_artifacts/training_weights"

REMOTE_TRAINING_WEIGHTS_PATH = get_env_var_or_default(
    var_name="REMOTE_TRAINING_WEIGHTS_PATH", 
    default_value="remote/path/to/your/weights/here"
)

LOCAL_TRAINING_WEIGHTS_CONFIG_PATH = f"models/{MODEL_NAME}/model_artifacts/training_weights/config.json"

REMOTE_TRAINING_WEIGHTS_CONFIG_PATH = get_env_var_or_default(
    var_name="REMOTE_TRAINING_WEIGHTS_CONFIG_PATH", 
    default_value="remote/path/to/your/weights/here"
)

N_SHARDS = 2
REMOTE_TRAINING_FILES_TO_DOWNLOAD = [
    f"model-{str(i+1).zfill(5)}-of-{str(N_SHARDS).zfill(5)}.safetensors"
    for i in range(N_SHARDS)
]

REMOTE_TRAINING_FILES_TO_DOWNLOAD += [
    "config.json",
    "generation_config.json",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "tokenizer.json",
    "tokenizer.model",
    "model.safetensors.index.json"
]


# -------------------------------

DEFAULT_PAD_TOKEN = "[PAD]"
DEFAULT_EOS_TOKEN = "</s>"
DEFAULT_BOS_TOKEN = "<s>"
DEFAULT_UNK_TOKEN = "</s>"


def log_memory_stuff(prompt=None):
    """One method to barf out everything we'd ever want to know about memory"""
    
    if prompt is not None:
        print(prompt)
    os.system("nvidia-smi")
    print(torch.cuda.memory_summary())


def load_tokenizer():
    """Same tokenizer, agnostic from tensorized weights/etc"""
    tok = LlamaTokenizer.from_pretrained(TOKENIZER_PATH, cache_dir="pretrained_weights", legacy=False)
    tok.add_special_tokens(
        {
            "eos_token": DEFAULT_EOS_TOKEN,
            "bos_token": DEFAULT_BOS_TOKEN,
            "unk_token": DEFAULT_UNK_TOKEN,
            "pad_token": DEFAULT_PAD_TOKEN,
        }
    )
    return tok

def download_file(file, local_filename):
    print(f"Downloading {file} to {local_filename}")
    if os.path.exists(local_filename):
        os.remove(local_filename)
    if '/' in local_filename:
        if not os.path.exists(os.path.dirname(local_filename)):
            os.makedirs(os.path.dirname(local_filename), exist_ok=True)
        
    command = ['pget', file, local_filename]
    subprocess.check_call(command)
    return

