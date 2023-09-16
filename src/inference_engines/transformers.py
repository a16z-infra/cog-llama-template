import os
import shutil
from transformers import AutoModelForCausalLM, TextIteratorStreamer, StoppingCriteria
from typing import Optional, List, Tuple, Any
from threading import Thread
from peft import PeftModel, LoraConfig
from peft.utils.save_and_load import set_peft_model_state_dict

import torch.nn.init
torch.nn.init.kaiming_uniform_ = lambda x, *args, **kwargs: x
torch.nn.init.uniform_ = lambda x, *args, **kwargs: x

import torch

from .engine import Engine

ADAPTER_NAME = "default"

class ExtraStopSequence(StoppingCriteria):
    """
    Adds in an extra stop sequence. Assuming 1-D generation, not batch. 
    """
    # TODO: there's something silly to debug here. 
    def __init__(self, stop_sequence: torch.Tensor, device: str):
        self.stop_sequence = stop_sequence.to(device)

    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor, **kwargs):
        return torch.equal(self.stop_sequence, input_ids[:, self.stop_sequence.shape[-1]])



class TransformersEngine(Engine):
    """
    An inference engine that runs in vanilla transformers. 
    Vanilla is, at times, fantastic.
    """
    def __init__(self, weights, tokenizer_func=None, device="cuda"):
        self.model = AutoModelForCausalLM.from_pretrained(weights).to(device)
        self.tokenizer = tokenizer_func()
        self.device = device 
        print("Transformers engine initialized.")


    def load_lora(self, lora_weights: dict)-> Tuple[LoraConfig, Any]:

        # serializing the dictionary of files and such - hf doesn't have quick and easy ways to load loras from file references, 
        # and this implementation isn't built for speed anyway
        model_dir = 'tmp/model'
        os.makedirs(model_dir)
        for handle in lora_weights:
            fpath = os.path.join(model_dir, handle)
            with open(fpath, 'wb') as f:
                f.write(lora_weights[handle])

        config = LoraConfig.from_pretrained(model_dir)
        weights = torch.load(
                os.path.join(model_dir, 'adapter_model.bin'), map_location=torch.device("cuda" if torch.cuda.is_available() else "cpu")
            )
        shutil.rmtree(model_dir)
        
        # # reset to non-lora model if previous lora exists, can't just swap loras out w/transformers
        # if hasattr(self.model, 'unload') and callable(self.model.unload):
        #     self.model = self.model.unload()


        # # todo - I can instantiate a peftconfig here and pass that, but alongside that we're still passing a string model name/path
        # model = PeftModel.from_pretrained(self.model, model_dir)
        # shutil.rmtree(model_dir)
        return (config, weights)


    def set_lora(self, lora):
        """
        Sets a new lora if needed. 
        """
        if lora is None:
            print("AN empty lora!!!")
            # reset to non-lora model, checking to see if model has ever been lora'd
            if hasattr(self.model, 'disable_adapter') and callable(self.model.disable_adapter):
                self.model.disable_adapter_layers()
                print("Disabled loras")
            return
        config, weights = lora

        # Note that right now we're just overwriting the "default" adapter w/ADAPTER_NAME 
        # we can try managing multiple adapters w/lru eviction logic, didn't seem necessary 
        if not hasattr(self.model, 'add_adapter'): 
        # is not a peft model
            self.model = PeftModel(self.model, config, ADAPTER_NAME)
            set_peft_model_state_dict(self.model, weights, ADAPTER_NAME)
            self.model.eval()
            print('added lora for the first time')
        else:
            self.model.enable_adapter_layers()
            self.model.add_adapter("0", config)
            set_peft_model_state_dict(self.model, weights, ADAPTER_NAME)
            print('set new lora')
            print(self.model.peft_config)

        return 
            

    def __call__(self, 
                 prompt,
                 max_new_tokens:int =128,
                 min_new_tokens:int =-1,
                 temperature:float =0.75,
                 top_p:float =0.9,
                 top_k:int =50,
                 stop_sequences: Optional[List[str]] = None,
                 **kwargs):
        tokens_in = self.tokenizer(prompt, return_tensors="pt").input_ids.to(self.device)
        streamer = TextIteratorStreamer(self.tokenizer, timeout=10.0, skip_prompt=True, skip_special_tokens=True)

        stopping_criteria_list = None
        if stop_sequences is not None:
            # stop sequences!
            stopping_criteria_list = []
            for seq in stop_sequences:
                stop_ids = self.tokenizer(seq, return_tensors='pt', add_special_tokens=False).input_ids[0]
                stopping_criteria_list.append(ExtraStopSequence(stop_ids, self.device))
   

        generate_kwargs = dict(
            input_ids=tokens_in,
            streamer=streamer,
            do_sample=True,
            max_new_tokens=max_new_tokens,
            min_new_tokens=min_new_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            stopping_criteria=stopping_criteria_list
        )

        t = Thread(target=self.model.generate, kwargs=generate_kwargs)
        t.start()

        for out in streamer:
            yield out
