CUDA_VISIBLE_DEVICES=2 python main_dclm.py \
    --model '/ossfs/workspace/datacube-nas/yixin_llm/DCLM-7B' \
    --prune_method DSnoT \
    --initial_method wanda \
    --seed 10 \
    --nsamples 1024 \
    --sparsity_ratio 0.5 \
    --sparsity_type unstructured \
    --max_cycle_time 50 \
    --update_threshold 0.1 \
    --pow_of_var_regrowing 1 \
    --save_model '/mntnlp/yixin.jyx/yixin.jyxx/yixin.jyx/output/dclm-DSnoT-wanda-0.5-slimpajama-seed10'