#!/bin/bash

model='path/to/Meta-Llama-3-8B'
#model='path/to/llama-v2-7b'
#model='path/to/llama-v2-13b'
data='c4'
sparsity_ratio=0.6
sparsity_type='unstructured'
nsamples=128
output_file="llama3_8b_${data}_ratio${sparsity_ratio}_type${sparsity_type}_nsamples${nsamples}_results.txt"


> $output_file

for seed in {0..19}; do
    seed_value=$((seed * 5))
    echo "Running with seed: $seed" >> $output_file
    echo "Running with calibration data: $data" >> $output_file
    CUDA_VISIBLE_DEVICES=0 python main.py \
        --model $model \
        --prune_method wanda \
        --seed $seed_value \
        --data $data \
        --nsamples $nsamples \
        --sparsity_ratio ${sparsity_ratio} \
        --sparsity_type ${sparsity_type} \
        --eval_zero_shot >> $output_file 2>&1
    echo "Finished running with seed: $seed" >> $output_file
    echo "------------------------" >> $output_file
done