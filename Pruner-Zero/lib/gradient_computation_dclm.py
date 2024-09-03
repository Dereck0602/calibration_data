import argparse
import csv
import os
import random
from importlib.metadata import version

import numpy as np
import torch
import torch.nn as nn
from open_lm.hf import *
from datasets import load_dataset, load_from_disk
from tqdm import tqdm
from transformers import (AdamW, AutoModelForCausalLM, AutoTokenizer,
                          LlamaTokenizer)

print('torch', version('torch'))
print('transformers', version('transformers'))
print('accelerate', version('accelerate'))
print('# of gpus: ', torch.cuda.device_count())


def find_layers(module, layers=[nn.Linear], name=''):
    if type(module) in layers:
        return {name: module}
    res = {}
    for name1, child in module.named_children():
        res.update(
            find_layers(
                child,
                layers=layers,
                name=name + '.' + name1 if name != '' else name1))
    return res


def set_seed(seed):
    np.random.seed(seed)
    torch.random.manual_seed(seed)


# Wrapper for tokenized input IDs
class TokenizerWrapper:

    def __init__(self, input_ids):
        self.input_ids = input_ids


# Load and process wikitext2 dataset
def get_wikitext2(nsamples, seed, seqlen, tokenizer):
    # Load train and test datasets
    # Load local dataset

    traindata = load_dataset('parquet', data_files='/ossfs/workspace/datacube-nas/yixin_llm/data/wikitext-2-raw-v1/train-00000-of-00001.parquet')
    testdata = load_dataset('parquet',data_files='/ossfs/workspace/datacube-nas/yixin_llm/data/wikitext-2-raw-v1/test-00000-of-00001.parquet')

    # Encode datasets
    trainenc = tokenizer(' '.join(traindata['train']['text']), return_tensors='pt')
    testenc = tokenizer('\n\n'.join(testdata['train']['text']), return_tensors='pt')

    # Generate samples from training set
    random.seed(seed)
    trainloader = []
    for _ in range(nsamples):
        i = random.randint(0, trainenc.input_ids.shape[1] - seqlen - 1)
        j = i + seqlen
        inp = trainenc.input_ids[:, i:j]
        tar = inp.clone()
        # tar[:, :-1] = -100
        trainloader.append((inp, tar))
    return trainloader, testenc


def get_dclm(nsamples, seed, seqlen, tokenizer):
    # Load train and validation datasets
    #traindata = load_from_disk('/ossfs/workspace/datacube-nas/yixin_llm/data/c4_train')
    #valdata = load_from_disk('/ossfs/workspace/datacube-nas/yixin_llm/data/c4_validation')
    dataset = load_dataset('parquet',data_files='/ossfs/workspace/yixin.jyx/data/dclm-micro/output_1.parquet')
    traindata = dataset['train'].select(range(10000))
    valdata = dataset['train'].select(range(300, 600))
    trainenc = tokenizer(' '.join(traindata['text']), return_tensors='pt')

    # Generate samples from training set
    random.seed(seed)
    trainloader = []
    for _ in range(nsamples):
        '''
        while True:
            i = random.randint(0, len(traindata) - 1)
            trainenc = tokenizer(traindata[i]['text'], return_tensors='pt')
            if trainenc.input_ids.shape[1] > seqlen:
                break
        '''
        i = random.randint(0, trainenc.input_ids.shape[1] - seqlen - 1)
        j = i + seqlen
        inp = trainenc.input_ids[:, i:j]
        tar = inp.clone()
        tar[:, :-1] = -100
        trainloader.append((inp, tar))

    valenc = tokenizer(' '.join(valdata[:1100]['text']), return_tensors='pt')
    valenc = valenc.input_ids[:, :(256 * seqlen)]
    valenc = TokenizerWrapper(valenc)
    return trainloader, valenc

def get_c4(nsamples, seed, seqlen, tokenizer):
    # Load train and validation datasets
    traindata = load_from_disk('/ossfs/workspace/datacube-nas/yixin_llm/data/c4_train')
    valdata = load_from_disk('/ossfs/workspace/datacube-nas/yixin_llm/data/c4_validation')

    # Generate samples from training set
    random.seed(seed)
    trainloader = []
    for _ in range(nsamples):
        while True:
            i = random.randint(0, len(traindata) - 1)
            trainenc = tokenizer(traindata[i]['text'], return_tensors='pt')
            if trainenc.input_ids.shape[1] > seqlen:
                break
        i = random.randint(0, trainenc.input_ids.shape[1] - seqlen - 1)
        j = i + seqlen
        inp = trainenc.input_ids[:, i:j]
        tar = inp.clone()
        tar[:, :-1] = -100
        trainloader.append((inp, tar))

    # Prepare validation dataset
    valenc = tokenizer(' '.join(valdata[:1100]['text']), return_tensors='pt')
    valenc = valenc.input_ids[:, :(256 * seqlen)]
    valenc = TokenizerWrapper(valenc)
    return trainloader, valenc


def get_slimpajama(nsamples, seed, seqlen, tokenizer):
    # Load train and validation datasets

    url='/ossfs/workspace/yixin.jyx/scale_lowrank/slimpajama/train/'
    file_pattern = 'chunk2_{i}.jsonl.zst'
    file_list = [url + file_pattern.format(i=i) for i in range(18)]   #(0,6)

    traindata = load_dataset("json", data_files={'train':file_list})['train']
    valdata = load_from_disk('/ossfs/workspace/datacube-nas/yixin_llm/data/c4_validation')

    # Generate samples from training set
    random.seed(seed)
    trainloader = []
    for _ in range(nsamples):
        while True:
            i = random.randint(0, len(traindata) - 1)
            trainenc = tokenizer(traindata[i]['text'], return_tensors='pt')
            if trainenc.input_ids.shape[1] > seqlen:
                break
        i = random.randint(0, trainenc.input_ids.shape[1] - seqlen - 1)
        j = i + seqlen
        inp = trainenc.input_ids[:, i:j]
        tar = inp.clone()
        tar[:, :-1] = -100
        trainloader.append((inp, tar))

    # Prepare validation dataset
    valenc = tokenizer(' '.join(valdata[:1100]['text']), return_tensors='pt')
    valenc = valenc.input_ids[:, :(256 * seqlen)]
    valenc = TokenizerWrapper(valenc)
    return trainloader, valenc

def get_wikipedia(nsamples, seed, seqlen, tokenizer):
    # Load train and validation datasets

    traindata = load_dataset("parquet", data_files='/ossfs/workspace/datacube-nas/yixin_llm/data/wikipedia/train-00000-of-00041.parquet')['train']
    valdata = load_from_disk('/ossfs/workspace/datacube-nas/yixin_llm/data/c4_validation')

    trainenc = tokenizer(' '.join(traindata['text']), return_tensors='pt')
    # Generate samples from training set
    random.seed(seed)
    trainloader = []
    for _ in range(nsamples):
        '''
        while True:
            i = random.randint(0, len(traindata) - 1)
            trainenc = tokenizer(traindata[i]['text'], return_tensors='pt')
            if trainenc.input_ids.shape[1] > seqlen:
                break
        '''
        i = random.randint(0, trainenc.input_ids.shape[1] - seqlen - 1)
        j = i + seqlen
        inp = trainenc.input_ids[:, i:j]
        tar = inp.clone()
        tar[:, :-1] = -100
        trainloader.append((inp, tar))

    # Prepare validation dataset
    valenc = tokenizer(' '.join(valdata[:1100]['text']), return_tensors='pt')
    valenc = valenc.input_ids[:, :(256 * seqlen)]
    valenc = TokenizerWrapper(valenc)
    return trainloader, valenc

# Function to select the appropriate loader based on dataset name
def get_loaders(name, nsamples=128, seed=0, seqlen=2048, tokenizer=None):
    if 'wikitext2' in name:
        return get_wikitext2(nsamples, seed, seqlen, tokenizer)
    if "dclm" in name:
        return get_dclm(nsamples, seed, seqlen, tokenizer)
    if "c4" in name:
        return get_c4(nsamples, seed, seqlen, tokenizer)
    if "slimpajama" in name:
        return get_slimpajama(nsamples, seed, seqlen, tokenizer)
    if 'wikipedia' in name:
        return get_wikipedia(nsamples, seed, seqlen, tokenizer)


def get_llm(model, cache_dir='llm_weights'):
    model = AutoModelForCausalLM.from_pretrained(
        model,
        torch_dtype=torch.float16,
        cache_dir=cache_dir,
        low_cpu_mem_usage=True,)
        #device_map='auto')
    print('printing gpu allocation for all the layers')
    
    model.seqlen = 2048
    return model


class GradientComputation:

    def __init__(self, model, scale):
        self.model = model
        self.gradients_l1 = dict()
        self.gradients_l2 = dict()
        self.nsample = 0
        self.scale = scale
        self.device = torch.device('cpu')
        self.gradients_init()

    def gradients_init(self):
        if 'OPT' in self.model.model.__class__.__name__:
            layers = self.model.model.decoder.layers
        else:
            layers = self.model.model.layers

        for i in tqdm(
                range(len(layers)),
                desc=f'initializing the gradient list ....'):
            layer = layers[i]
            subset = find_layers(layer)
            for name in subset:
                indexed_name = f'{name}_layer_{i}'
                self.gradients_l1[indexed_name] = torch.zeros_like(
                    subset[name].weight,
                    dtype=torch.float16,
                    device=self.device)
                self.gradients_l2[indexed_name] = torch.zeros_like(
                    subset[name].weight,
                    dtype=torch.float32,
                    device=self.device)

    def update_gradient(self, model, nsample):
        assert nsample - self.nsample == 1, 'number of samples must be incremented by 1'
        if 'OPT' in model.model.__class__.__name__:
            layers = model.model.decoder.layers
        else:
            layers = model.model.layers
        for i in tqdm(
                range(len(layers)),
                desc=f'updating the gradient of sample no: {self.nsample}'):
            layer = layers[i]
            subset = find_layers(layer)
            for name in subset:
                indexed_name = f'{name}_layer_{i}'
                if subset[name].weight.grad is None:
                    print(f'Error: {name} has none gradient')
                if subset[name].weight.grad is not None:
                    assert subset[
                        name].weight.requires_grad == True, f'Required grad must be true ( {name}: {subset[name].weight.requires_grad})'
                    grad = subset[name].weight.grad.detach().clone().to(
                        dtype=torch.float32)  # Cast to float32
                    all_zero = (torch.abs(grad) == 0).all()
                    assert int(
                        all_zero
                    ) == 0, f'all the elements in the tensor are zero.: {all_zero}'
                    assert self.gradients_l1[
                        indexed_name].shape == grad.shape, 'shape mismatch'
                    self.gradients_l1[indexed_name] = self.gradients_l1[
                        indexed_name] + torch.abs(grad * self.scale).to(
                            device=self.device).to(dtype=torch.float16)
                    self.gradients_l2[indexed_name] = self.gradients_l2[
                        indexed_name] + torch.abs(
                            (grad * self.scale)**2).to(device=self.device)
        self.nsample = nsample


def get_activation(name, activations):
    """ Function to return a hook that stores the output of the layer in the provided dictionary. """

    def hook(model, input, output):
        if isinstance(output, tuple):
            output = output[0]
        activations[name] = output.detach()

    return hook





if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--nsamples', type=int, default=2, help='no of samples used')
    parser.add_argument(
        '--scale', type=int, default=100, help='no of samples used')
    parser.add_argument('--model', type=str, help='model to used')
    parser.add_argument(
        '--task', type=str, default='gradient', help='task to be performed')
    parser.add_argument(
        '--data', type=str, default='wikitext', help='calibration data')
    parser.add_argument('--seed', type=int, default=0, help='seed used')
    args = parser.parse_args()
    print(
        f'Obtaining gradients for no of samples {args.nsamples}, scale {args.scale}'
    )

    model_args = args.model
    cache_dir_args = 'llm_weights'
    model = get_llm(model_args, cache_dir_args)
    
    tokenizer = AutoTokenizer.from_pretrained(model_args,trust_remote_code=True)
    
    for i in range(model.config.n_layers):
        in_proj = model.model.layers[i].attention.in_proj.weight
        q_proj, k_proj, v_proj = in_proj.chunk(3,dim=0)
        model.model.layers[i].attention.q_proj.weight = nn.Parameter(q_proj)
        model.model.layers[i].attention.k_proj.weight = nn.Parameter(k_proj)
        model.model.layers[i].attention.v_proj.weight = nn.Parameter(v_proj)
        del model.model.layers[i].attention.in_proj

        w12 = model.model.layers[i].feed_forward.w12.weight
        w1, w2 = w12.chunk(2,dim=0)
        model.model.layers[i].feed_forward.w1.weight = nn.Parameter(w1)
        model.model.layers[i].feed_forward.w2.weight = nn.Parameter(w2)
        del model.model.layers[i].feed_forward.w12
    device = torch.device("cuda:0")
    model.to(device)
    layers = model.model.layers

    
    
    
    print('loading calibdation data')
    nsamples = args.nsamples
    seed = args.seed
    dataloader, _ = get_loaders(
        args.data,
        nsamples=nsamples,
        seed=seed,
        seqlen=2048,
        tokenizer=tokenizer)

    print('dataset loading complete')
    optimizer = AdamW(model.parameters(), lr=0.01, eps=0.01)
    optimizer.zero_grad()

    
    computer = GradientComputation(model, args.scale)
    

    nsample = 0
    model.train()

    for input_ids, labels in tqdm(dataloader):
        nsample += 1
        input_ids = input_ids.to(device)
        labels = labels.to(device)
        outputs = model(input_ids=input_ids, labels=labels)
        loss = outputs.loss
        # print('Printing the loss:', loss)
        loss.backward()

        computer.update_gradient(model, nsample)
        

        optimizer.zero_grad()
    print('Done')

    model_name = os.path.basename(args.model)

    
    gradients_l2 = computer.gradients_l2
    for name in gradients_l2:
        grad_sqrt = torch.sqrt(gradients_l2[name])
        gradients_l2[name] = grad_sqrt.to(dtype=torch.float16)

    
    
    if not os.path.exists('/ossfs/workspace/datacube-nas/yixin_llm/gradients/dclm'):
        os.makedirs(f'/ossfs/workspace/datacube-nas/yixin_llm/gradients/dclm')

    with open(f'/ossfs/workspace/datacube-nas/yixin_llm/gradients/dclm/gradients_aggregate_norm_l2_{model_name}_{args.data}_{args.seed}.path', 'wb') as f:
        torch.save(computer.gradients_l2, f)
        
            

    