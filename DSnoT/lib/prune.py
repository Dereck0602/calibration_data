import time

import torch
import torch.nn as nn
import transformers

from .save_results import save_time_result
from .data import get_loaders
from .layerwrapper import WrappedGPT
from datasets import load_dataset


def find_layers(module, layers=[nn.Linear], name=""):
    """
    Recursively find the layers of a certain type in a module.

    Args:
        module (nn.Module): PyTorch module.
        layers (list): List of layer types to find.
        name (str): Name of the module.

    Returns:
        dict: Dictionary of layers of the given type(s) within the module.
    """
    if type(module) in layers:
        return {name: module}
    res = {}
    for name1, child in module.named_children():
        res.update(
            find_layers(
                child, layers=layers, name=name + "." + name1 if name != "" else name1
            )
        )
    return res


def check_sparsity(model):
    use_cache = model.config.use_cache
    model.config.use_cache = False

    layers = model.model.layers
    count = 0
    total_params = 0
    for i in range(len(layers)):
        layer = layers[i]
        subset = find_layers(layer)

        sub_count = 0
        sub_params = 0
        for name in subset:
            W = subset[name].weight.data
            count += (W == 0).sum().item()
            total_params += W.numel()

            sub_count += (W == 0).sum().item()
            sub_params += W.numel()

        print(f"layer {i} sparsity {float(sub_count)/sub_params:.6f}")

    model.config.use_cache = use_cache
    return float(count) / total_params


def prepare_calibration_input(args, model, dataloader, device):
    use_cache = model.config.use_cache
    model.config.use_cache = False
    layers = model.model.layers

    if "model.embed_tokens" in model.hf_device_map:
        device = model.hf_device_map["model.embed_tokens"]

    dtype = next(iter(model.parameters())).dtype
    inps = torch.zeros(
        (args.nsamples, model.seqlen, model.config.hidden_size),
        dtype=dtype,
        device=device,
    )
    inps.requires_grad = False
    cache = {"i": 0, "attention_mask": None, "position_ids": None}

    class Catcher(nn.Module):
        def __init__(self, module):
            super().__init__()
            self.module = module

        def forward(self, inp, **kwargs):
            inps[cache["i"]] = inp
            cache["i"] += 1
            cache["attention_mask"] = kwargs["attention_mask"]
            cache["position_ids"] = kwargs["position_ids"]
            raise ValueError

    layers[0] = Catcher(layers[0])
    for batch in dataloader:
        try:
            model(batch[0].to(device))
        except ValueError:
            pass
    layers[0] = layers[0].module

    outs = torch.zeros_like(inps)
    attention_mask = cache["attention_mask"]
    position_ids = cache["position_ids"]
    model.config.use_cache = use_cache

    return inps, outs, attention_mask, position_ids


def return_reorder_indice(input_tensor):
    """
    For instance:
    [[1., -2., 3.],
    [-2, 2., -4],
    [5., 6., -7],
    [-6, -7, -4]]
    return indices of
    [[-2.,  3.,  1.],
    [-2., -4.,  2.],
    [-7.,  6.,  5.],
    [-6., -7., -4.]]
    Description: The relative order in the positive number remains unchanged, and the relative order in the negative number is flipped.
    """
    positive_tensor = input_tensor.clone()
    negative_tensor = input_tensor.clone()

    positive_mask = positive_tensor > 0
    negative_mask = negative_tensor < 0

    positive_indices = (
        torch.arange(0, input_tensor.shape[1], device=input_tensor.device)
        .to(torch.float64)
        .repeat(input_tensor.shape[0], 1)
    )
    negative_indices = (
        torch.arange(0, input_tensor.shape[1], device=input_tensor.device)
        .to(torch.float64)
        .repeat(input_tensor.shape[0], 1)
    )

    positive_indices[~positive_mask] = float("inf")
    negative_indices[~negative_mask] = float("inf")

    positive_value, _ = torch.sort(positive_indices, dim=1)
    negative_value, _ = torch.sort(negative_indices, dim=1)

    positive_value = torch.flip(positive_value, dims=[1])

    negative_value[negative_value == float("inf")] = 0
    positive_value[positive_value == float("inf")] = 0

    reorder_indice = (positive_value + negative_value).to(torch.int64)

    return reorder_indice



def prune_DSnoT(
    args, model, tokenizer, device=torch.device("cuda:0"), prune_n=0, prune_m=0
):
    use_cache = model.config.use_cache
    model.config.use_cache = False

    print("loading calibdation data")
    if 'sampled' in args.data:
        data = load_dataset('json', data_files=args.sampled_path)['train']
        if args.sample_mode=='detect':
            scores = pd.read_csv(args.sample_weight)[args.sample_score]
            threshold = np.percentile(scores, args.sample_topk*100)
            top_scores = scores[scores >= threshold]
            exp_top_scores = np.exp(top_scores)
            prob = exp_top_scores / np.sum(exp_top_scores)
            selected_indices = np.where(scores >= threshold)[0]
            selected_samples = np.random.choice(selected_indices, size=128, p=prob)
            dataloader = [(process_input_ids(tokenizer(data[int(i)]['text'], return_tensors='pt',padding=True,max_length=2048).input_ids),) for i in selected_samples]
        else:
            '''
            print('self_generate')
            scores = pd.read_csv(args.sample_weight)['loss']
            threshold = np.percentile(scores, 95)
            selected_indices = np.where(scores >= threshold)[0]
            selected_samples = np.random.choice(selected_indices, size=128)
            '''
            dataloader, _ = get_loaders(args.data,nsamples=args.nsamples,seed=args.seed,seqlen=model.seqlen,tokenizer=tokenizer,data_path=args.sampled_path)
            
        #dataloader = sample(data,args.nsamples)
    else:
        dataloader, _ = get_loaders(args.data,nsamples=args.nsamples,seed=args.seed,seqlen=model.seqlen,tokenizer=tokenizer)
    print("dataset loading complete")
    with torch.no_grad():
        inps, outs, attention_mask, position_ids = prepare_calibration_input(
            args, model, dataloader, device
        )

    total_time = 0
    layers = model.model.layers
    for i in range(len(layers)):
        layer = layers[i]
        subset = find_layers(layer)

        if (f"model.layers.{i}" in model.hf_device_map):  ## handle the case for llama-30B and llama-65B, when the device map has multiple GPUs;
            dev = model.hf_device_map[f"model.layers.{i}"]
            inps, outs, attention_mask, position_ids = (
                inps.to(dev),
                outs.to(dev),
                attention_mask.to(dev),
                position_ids.to(dev),
            )

        wrapped_layers = {}
        for name in subset:
            wrapped_layers[name] = WrappedGPT(
                subset[name],
                initial_method=args.initial_method
            )

        def add_batch(name):
            def tmp(_, inp, out):
                wrapped_layers[name].add_batch(inp[0].data, out.data)

            return tmp

        handles = []
        for name in wrapped_layers:
            handles.append(subset[name].register_forward_hook(add_batch(name)))
        for j in range(args.nsamples):
            with torch.no_grad():
                outs[j] = layer(
                    inps[j].unsqueeze(0),
                    attention_mask=attention_mask,
                    position_ids=position_ids,
                )[0]
        for h in handles:
            h.remove()

        for name in subset:
            print(f"pruning layer {i} name {name}")
            start_time = time.time()

            DSnoT_metric = subset[name].weight.data * wrapped_layers[name].sum_metric_row.reshape((1, -1))   #1*N

            if args.initial_method == "wanda":
                initial_metric = torch.abs(subset[name].weight.data) * torch.sqrt(
                    wrapped_layers[name].scaler_row.reshape((1, -1))
                )
            elif args.initial_method == "magnitude":
                initial_metric = torch.abs(subset[name].weight.data)
            elif args.initial_method == "sparsegpt":
                W = subset[name].weight.data.clone()
                if isinstance(subset[name], nn.Conv2d):
                    W = W.flatten(1)
                if isinstance(subset[name], transformers.Conv1D):
                    W = W.t()
                W = W.float()

                H = wrapped_layers[name].H
                # del wrapped_layers[name].H
                dead = torch.diag(H) == 0
                H[dead, dead] = 1
                W[:, dead] = 0

                percdamp = 0.01
                damp = percdamp * torch.mean(torch.diag(H))
                diag = torch.arange(
                    wrapped_layers[name].columns, device=wrapped_layers[name].dev
                )
                H[diag, diag] += damp
                H = torch.linalg.cholesky(H)
                H = torch.cholesky_inverse(H)
                H = torch.linalg.cholesky(H, upper=True)
                Hinv = H

                initial_metric = W**2 / (torch.diag(Hinv).reshape((1, -1))) ** 2

            weight_mask = torch.zeros_like(initial_metric) == 1

            if prune_n != 0:
                if (name.split(".")[0] == args.skip_layer or name.split(".")[1] == args.skip_sub_layer):
                    for ii in range(initial_metric.shape[1]):
                        if ii % prune_m == 0:
                            tmp = initial_metric[:, ii : (ii + prune_m)].float()
                            weight_mask.scatter_(1, ii + torch.topk(tmp, prune_n, dim=1, largest=False)[1], True,)
                else:
                    initial_prune_indices = torch.zeros((initial_metric.shape[0], 0), dtype=torch.int64, device=initial_metric.device,)
                    initial_res_indices = torch.zeros((initial_metric.shape[0], 0), dtype=torch.int64, device=initial_metric.device,)

                    for ii in range(initial_metric.shape[1]):
                        if ii % prune_m == 0:
                            tmp = initial_metric[:, ii : (ii + prune_m)].float()
                            _, tmp_all_indices = torch.sort(tmp, dim=1)
                            tmp_all_indices += ii
                            res_prune_n = prune_m - prune_n
                            tmp_indices, tmp_res_indices = torch.split(
                                tmp_all_indices,
                                split_size_or_sections=[prune_n, res_prune_n],
                                dim=1,
                            )

                            initial_prune_indices = torch.cat(
                                (initial_prune_indices, tmp_indices), dim=1
                            )
                            initial_res_indices = torch.cat(
                                (initial_res_indices, tmp_res_indices), dim=1
                            )
                            weight_mask.scatter_(1, tmp_indices, True)

                    metric_for_regrowing = DSnoT_metric.clone()

                    metric_for_regrowing.scatter_(1, initial_res_indices, 0)

                    reconstruction_error = torch.sum(metric_for_regrowing, dim=1, keepdim=True)
                    initialize_error_sign = torch.sign(reconstruction_error)

                    if args.pow_of_var_regrowing:
                        metric_for_regrowing /= torch.pow(
                            wrapped_layers[name].var.reshape((1, -1)),
                            args.pow_of_var_regrowing,
                        )

                    _, regrowing_indices_block = torch.sort(metric_for_regrowing, dim=1, stable=True)

                    indice_indice_list_for_regrowing = torch.zeros(
                        (reconstruction_error.shape[0], 2),
                        device=reconstruction_error.device,
                        dtype=torch.long,
                    )
                    last_one = regrowing_indices_block.shape[-1] - 1
                    indice_indice_list_for_regrowing[:, 1] = last_one
                    update_num_for_regrowing = torch.ones(
                        (reconstruction_error.shape[0], 2),
                        device=reconstruction_error.device,
                        dtype=torch.long,
                    )
                    update_num_for_regrowing[:, 1] = -1

                    initial_metric.scatter_(1, initial_prune_indices, float("inf"))
                    W_metric_max_value = (torch.max(initial_metric, dim=1, keepdim=True)[0] + 1)

                    cycle_time = 1
                    update_mask = torch.ones_like(
                        reconstruction_error, dtype=torch.bool
                    )
                    while not (
                        torch.all(update_mask == False)
                        or cycle_time > args.max_cycle_time
                    ):
                        cycle_time += 1

                        # regrowing
                        indice_of_indice_indice_list_for_regrowing = (
                            (reconstruction_error > 0).int().to(torch.int64)
                        )
                        indice_indice_for_regrowing = torch.gather(
                            indice_indice_list_for_regrowing,
                            1,
                            indice_of_indice_indice_list_for_regrowing,
                        )

                        regrowing_indice = torch.gather(
                            regrowing_indices_block,
                            1,
                            indice_indice_for_regrowing.to(torch.int64),
                        )

                        regrowing_metric = DSnoT_metric.gather(
                            1, regrowing_indice.to(torch.int64)
                        )

                        recover_block_start_indice = (
                            regrowing_indice - regrowing_indice % prune_m
                        )

                        recover_block_indices = (
                            torch.arange(
                                0, prune_m, device=recover_block_start_indice.device
                            ).repeat(recover_block_start_indice.shape[1], 1)
                            + recover_block_start_indice
                        )

                        pruning_block = torch.gather(
                            initial_metric, 1, recover_block_indices.to(torch.int64)
                        )

                        pruning_wanda_metric, pruning_indice = torch.topk(
                            pruning_block, 1, dim=1, largest=False
                        )

                        pruning_indice += recover_block_start_indice

                        
                        pruning_metric = DSnoT_metric.gather( 1, pruning_indice.to(torch.int64) )
                        

                        reconstruction_error_after = ( reconstruction_error + pruning_metric - regrowing_metric )

                        update_mask = (update_mask & ( initialize_error_sign == torch.sign(reconstruction_error_after) ) & ( abs(reconstruction_error) > args.update_threshold))

                        initial_metric.scatter_(1, pruning_indice, W_metric_max_value)

                        weight_mask.scatter_(1, pruning_indice, update_mask)

                        weight_mask.scatter_(1, regrowing_indice, ~update_mask)

                        reconstruction_error += torch.where(
                            update_mask,
                            pruning_metric,
                            torch.zeros_like(pruning_metric),
                        )
                        reconstruction_error -= torch.where(
                            update_mask,
                            regrowing_metric,
                            torch.zeros_like(regrowing_metric),
                        )

                        indice_indice_list_for_regrowing.scatter_(
                            1,
                            indice_of_indice_indice_list_for_regrowing,
                            indice_indice_for_regrowing
                            + update_num_for_regrowing.gather(
                                1, indice_of_indice_indice_list_for_regrowing
                            ),
                        )
            else:
                _, sorted_initial_indice = torch.sort(  #对initial_metric进行排序，并返回排序后的索引
                    initial_metric, dim=-1, stable=True
                )

                sparsity_num = int(initial_metric.shape[1] * args.sparsity_ratio)
                res_sparsity_num = sorted_initial_indice.shape[1] - sparsity_num

                initial_prune_indices, initial_res_indices = torch.split(  #将sorted_initial_indice张量在列方向上分割成两个张量initial_prune_indices和initial_res_indices，其中initial_prune_indices包含前sparsity_num个元素，initial_res_indices包含后res_sparsity_num个元素
                    sorted_initial_indice,
                    split_size_or_sections=[sparsity_num, res_sparsity_num],
                    dim=1,
                )

                if (
                    name.split(".")[0] == args.skip_layer
                    or name.split(".")[1] == args.skip_sub_layer
                    or args.without_DSnoT
                ):
                    weight_mask.scatter_(1, initial_prune_indices, True)

                else:
                    weight_mask.scatter_(1, initial_prune_indices, True)  #scatter_函数将weight_mask张量在第1维（即列）上，根据initial_prune_indices张量中的索引进行更新，将对应位置的值设置为True

                    metric_for_regrowing = DSnoT_metric.clone()
                    wanda_metric = torch.abs(subset[name].weight.data) * torch.sqrt(
                        wrapped_layers[name].scaler_row.reshape((1, -1))
                    )

                    metric_for_regrowing.scatter_(1, initial_res_indices, 0)
                    reconstruction_error = torch.sum(
                        metric_for_regrowing, dim=1, keepdim=True
                    )
                    initialize_error_sign = torch.sign(reconstruction_error)

                    if args.pow_of_var_regrowing:
                        metric_for_regrowing /= torch.pow(
                            wrapped_layers[name].var.reshape((1, -1)),
                            args.pow_of_var_regrowing,
                        )

                    _, regrowing_indices_block = torch.sort(
                        metric_for_regrowing, dim=1, stable=True
                    )

                    wanda_metric.scatter_(1, initial_prune_indices, float("inf"))
                    wanda_res_indices, _ = torch.split(
                        torch.sort(wanda_metric, dim=1, stable=True)[1],
                        split_size_or_sections=[res_sparsity_num, sparsity_num],
                        dim=1,
                    )
                    reorder_indice_of_pruning_indice = return_reorder_indice(
                        torch.gather(DSnoT_metric, 1, wanda_res_indices)
                    )
                    pruning_indices_block = torch.gather(
                        wanda_res_indices, 1, reorder_indice_of_pruning_indice
                    )

                    indice_indice_list_for_regrowing = torch.zeros(
                        (reconstruction_error.shape[0], 2),
                        device=reconstruction_error.device,
                        dtype=torch.long,
                    )
                    last_one = regrowing_indices_block.shape[-1] - 1
                    indice_indice_list_for_regrowing[:, 1] = last_one

                    update_num_for_regrowing = torch.ones(
                        (reconstruction_error.shape[0], 2),
                        device=reconstruction_error.device,
                        dtype=torch.long,
                    )
                    update_num_for_regrowing[:, 1] = -1

                    indice_indice_list_for_pruning = torch.zeros(
                        (reconstruction_error.shape[0], 2),
                        device=reconstruction_error.device,
                        dtype=torch.long,
                    )
                    last_one = pruning_indices_block.shape[-1] - 1
                    indice_indice_list_for_pruning[:, 1] = last_one

                    update_num_for_pruning = torch.ones(
                        (reconstruction_error.shape[0], 2),
                        device=reconstruction_error.device,
                        dtype=torch.long,
                    )
                    update_num_for_pruning[:, 1] = -1

                    update_mask = torch.ones_like(
                        reconstruction_error, dtype=torch.bool
                    )
                    cycle_time = 0
                    while not ( torch.all(update_mask == False) or cycle_time >= args.max_cycle_time ):
                        cycle_time += 1
                        
                        # regrowing
                        indice_of_indice_indice_list_for_regrowing = (
                            (reconstruction_error > 0).int().to(torch.int64)
                        )

                        indice_indice_for_regrowing = torch.gather(
                            indice_indice_list_for_regrowing,
                            1,
                            indice_of_indice_indice_list_for_regrowing,
                        )

                        regrowing_indice = torch.gather(
                            regrowing_indices_block,
                            1,
                            indice_indice_for_regrowing.to(torch.int64),
                        )

                        regrowing_metric = DSnoT_metric.gather(
                            1, regrowing_indice.to(torch.int64)
                        )

                        indice_indice_list_for_regrowing.scatter_(
                            1,
                            indice_of_indice_indice_list_for_regrowing,
                            indice_indice_for_regrowing
                            + update_num_for_regrowing.gather(
                                1, indice_of_indice_indice_list_for_regrowing
                            ),
                        )

                        # pruning
                        indice_of_indice_indice_list_for_pruning = (
                            (reconstruction_error < 0).int().to(torch.int64)
                        )

                        indice_indice_for_pruning = torch.gather(
                            indice_indice_list_for_pruning,
                            1,
                            indice_of_indice_indice_list_for_pruning,
                        )

                        pruning_indice = torch.gather(
                            pruning_indices_block,
                            1,
                            indice_indice_for_pruning.to(torch.int64),
                        )

                        pruning_metric = DSnoT_metric.gather(
                            1, pruning_indice.to(torch.int64)
                        )

                        indice_indice_list_for_pruning.scatter_(
                            1,
                            indice_of_indice_indice_list_for_pruning, 
                            indice_indice_for_pruning
                            + update_num_for_pruning.gather(
                                1, indice_of_indice_indice_list_for_pruning
                            ),
                        )

                        # change mask
                        reconstruction_error_after = (
                            reconstruction_error + pruning_metric - regrowing_metric
                        )

                        if args.without_same_sign == str(True):
                            update_mask = update_mask & (
                                abs(reconstruction_error) > args.update_threshold
                            )
                        else:
                            update_mask = (
                                update_mask
                                & (abs(reconstruction_error) > args.update_threshold)
                                & (
                                    initialize_error_sign
                                    == torch.sign(reconstruction_error_after)
                                )
                            )

                        weight_mask.scatter_(1, pruning_indice, update_mask)
                        weight_mask.scatter_(1, regrowing_indice, ~update_mask)

                        reconstruction_error += torch.where(
                            update_mask,
                            pruning_metric,
                            torch.zeros_like(pruning_metric),
                        )
                        reconstruction_error -= torch.where(
                            update_mask,
                            regrowing_metric,
                            torch.zeros_like(regrowing_metric),
                        )

            
            subset[name].weight.data[weight_mask] = 0

            end_time = time.time()
            total_time += end_time - start_time

        for j in range(args.nsamples):
            with torch.no_grad():
                outs[j] = layer(
                    inps[j].unsqueeze(0),
                    attention_mask=attention_mask,
                    position_ids=position_ids,
                )[0]
        inps, outs = outs, inps

    if args.get_time_overhead:
        save_time_result(args, args.output_results_file, total_time)

    model.config.use_cache = use_cache
    torch.cuda.empty_cache()


