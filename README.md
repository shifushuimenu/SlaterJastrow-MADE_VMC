# Autoregressive Slater-Jastrow ansatz for variational Monte Carlo [arXiv:2210.05871](https://arxiv.org/abs/2210.05871)
<img align="middle" src="_misc/arSJ_sketch.png" width="650" alt="sketch"/>
</div>

---
As a demonstration the t-V model of spinless fermions on the square lattice is implemented. 

## Key features
 - Get rid of autocorrelation time completely !
 - Fast **direct sampling** from a Slater determinant
 - Jastrow factor represented by autoregressive neural network
 - Lowrank update for local kinetic energy preserves cubic scaling 

## How to run the code
Run VMC for t-V model on a square 4x4 lattice with 8 spinless fermions and interaction strength V/t=4.0;
training iteractions = 1000; batch size per iteration = 200; num. of samples in measurement phase = 300.
```python
python3 -O ./run_autoregVMC.py 4 4 8 4.0 1000 200 300 --optimizer Adam --seed 42 --optimize_orbitals True
```
For a full list of options see
```python
python3 ./run_autoregVMC.py --help
```
## Benchmarking 
```python 
python3 ./ED/ED_spinless_fermions_tV_2d.py 4 4 8 4.0
```
This requires installation of the [QuSpin](http://weinbe58.github.io/QuSpin/) library.

## Note 
If run in debug mode (i.e. without -O flag) the lowrank update rarely throws an error due 
to limited floating point precision. This could be avoided using mpmath infinite precision library.

## Cite
```
@article{humeniuk2022autoregressive,
  title={Autoregressive neural Slater-Jastrow ansatz for variational Monte Carlo simulation},
  author={Humeniuk, Stephan and Wan, Yuan and Wang, Lei},
  journal={arXiv preprint arXiv:2210.05871},
  year={2022}
}
```
