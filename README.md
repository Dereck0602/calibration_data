<div align="center">
<h1>Calibration Data</h1>
</div>

This repo contains the code for "Beware of Calibration Data for Pruning Large Language Models" ([https://arxiv.org/abs/2410.17711](https://arxiv.org/abs/2410.17711))

## Quick start

### Data
https://drive.google.com/file/d/1d37_LYKEXF_rozdbqSSpDU-u0lgsno__/view?usp=drive_link

The file ``wikipedia_5k.json`` contains 5,000 raw texts, while ``wikipedia_dclm_selfgen_filter.json`` and ``wikipedia_qwen_selfgen_filter.json`` are datasets generated and filtered from the raw texts using DCLM-7B and Qwen2.5-7B, respectively.

### Installation
```
pip install -r requirement.txt
```

### Empirical study
```
cd Wanda
bash run_dclm_wanda.sh
```

### Self-generating synthetic data
```
bash run_gendata.sh
python sample_ppl.py
```

### Evaluation
```
cd Wanda
bash run_dclm_wanda_sample.sh
```
