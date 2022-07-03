# IMPROVE: Organize imports globally in a better way
#          when making a package with an  __init__.py file. 
import numpy as np
import torch 
from utils import default_dtype_torch 

from one_hot import occ_numbers_collapse
from monitoring_old import logger 
from Slater_Jastrow_simple import vmc_measure, PhysicalSystem, VMCKernel
from SlaterJastrow_ansatz import SlaterJastrow_ansatz
#from slater_sampler_ordered_memory_layout import SlaterDetSampler_ordered
from slater_sampler_ordered import SlaterDetSampler_ordered
from test_suite import HartreeFock_tVmodel

import itertools
from time import time 
import argparse 

Parallel = False 

if Parallel:
    from mpi4py import MPI
    comm = MPI.COMM_WORLD
    MPI_rank = comm.Get_rank()
    MPI_size = comm.Get_size()
    master = 0
    print("MPI: proc %d of %d" % (MPI_rank, MPI_size))
else:
    MPI_rank = 0
    MPI_size = 1
    master = 0

torch.set_default_dtype(default_dtype_torch)
torch.autograd.set_detect_anomaly(True)

# set random number seed
use_cuda = False
seed = 44 + MPI_rank
torch.manual_seed(seed)
if use_cuda: torch.cuda.manual_seed_all(seed)
np.random.seed(seed)

desc_str="VMC with autoregressive Slater-Jastrow ansatz for t-V model of spinless fermions"
parser = argparse.ArgumentParser(description=desc_str)
parser.add_argument('Lx', type=int, help='width of square lattice')
parser.add_argument('Ly', type=int, help='height of square lattice')
parser.add_argument('Np', metavar='N', type=int, help='number of particles')
parser.add_argument('Vint', metavar='V/t', type=float, help='nearest-neighbour interaction (V/t > 0 is repulsive)')
parser.add_argument('max_iter', metavar='max_epochs', type=int, help="number of training epochs")
parser.add_argument('num_samples', type=int, help="number of samples per epoch")
parser.add_argument('num_meas_samples', type=int, help="number of samples in measurement phase")
parser.add_argument('--optimize_orbitals', type=bool, default=False, help="co-optimize orbitals of Slater determinant (default=False)")
args = parser.parse_args()

Lx = args.Lx # 5  # 15
Ly = args.Ly # 5
Nparticles = args.Np # 12
Vint = args.Vint #  Vint_array[MPI_rank]
max_iter = args.max_iter # 10 #1000 
num_samples = args.num_samples # 10 # 100  # samples per batch
num_bin = num_samples // 20 # 50 
num_meas_samples = args.num_meas_samples
optimize_orbitals = args.optimize_orbitals  # whether to include columns of P-matrix in optimization
learning_rate_SD = 0.02

Nsites = Lx*Ly  # 15  # Nsites = 64 => program killed because it is using too much memory
space_dim = 2
paramstr = "_Lx{}Ly{}Np{}V{}".format(Lx, Ly, Nparticles, Vint)
logger.info_refstate.outfile = "lowrank_stats"+paramstr+".dat"

# for debugging:
# If deactivate_Jastrow == True, samples are drawn from the Slater determinant without the Jastrow factor. 
deactivate_Jastrow = False


def train(VMCmodel, learning_rate, learning_rate_SD, num_samples=100, num_bin=50, use_cuda=False):
    '''
    train a model using stochastic gradient descent 

    Args:
        model (obj): a model that meets VMC model definition.
        learning_rate (float): the learning rate for SGD.
    '''
    if use_cuda:
        VMCmodel.ansatz.cuda()

    while True:
        # get expectation values for energy, gradient and their product,
        # as well as the precision of energy.        
        sample_list = np.zeros((num_samples, Nsites)) 
        log_probs = np.zeros((num_samples,))
        print("before sampling")
        with torch.no_grad():
            t1 = time()
            for i in range(num_samples):
                sample_unfolded, log_prob_sample = VMCmodel.ansatz.sample_unfolded()
                log_probs[i] = log_prob_sample
                sample_list[i] = occ_numbers_collapse(sample_unfolded, Nsites).numpy()
            t2 = time()
            VMCmodel.t_sampling += (t2-t1)

        energy, grad, energy_grad, precision = vmc_measure(VMCmodel.local_measure, sample_list, log_probs, num_bin=num_bin)

        # update variables using stochastic gradient descent
        # grad = <H Delta> - <H><Delta>
        g_list = [eg - energy * g for eg, g in zip(energy_grad, grad)]
        for (name, par), g in zip(VMCmodel.ansatz.named_parameters(), g_list):
            if name == 'slater_sampler.P':
                delta = learning_rate_SD * g
            else:
                delta = learning_rate * g
            par.data -= delta

        # re-orthogonalize the columns of the Slater determinant
        # and update bias of the zero-th component 
        if isinstance(VMCmodel.ansatz, SlaterJastrow_ansatz):
            print("slater_sampler.P.grad=", VMCmodel.ansatz.slater_sampler.P.grad)
            print("named parameters", list(VMCmodel.ansatz.named_parameters()))

            if VMCmodel.ansatz.slater_sampler.optimize_orbitals:
                VMCmodel.ansatz.slater_sampler.reortho_orbitals()

            VMCmodel.ansatz.slater_sampler.reset_sampler()
            VMCmodel.ansatz.bias_zeroth_component[:] = VMCmodel.ansatz.slater_sampler.get_cond_prob(k=0)

        yield energy, precision
        
        

# visualize the loss history
energy_list, precision_list = [], []
av_list, sigma_list = [], []
def _update_curve(energy, precision):
    energy_list.append(energy)
    precision_list.append(precision)
    Nb = len(energy_list)
    av = np.sum(energy_list) / Nb
    sigma = np.sqrt((np.sum([s**2 for s in precision_list]) / Nb) / Nb)
    av_list.append(av)
    sigma_list.append(sigma)
    if len(energy_list)%(max_iter-1) == 0:
        xvals = np.arange(1, len(energy_list) + 1)

    MM = np.hstack((np.array(energy_list)[:,None], np.array(precision_list)[:,None],
                    np.array(av_list)[:,None], np.array(sigma_list)[:,None]))
    np.savetxt("energies"+paramstr+".dat", MM)


ckpt_outfile = "state"+paramstr+".pt"
def _checkpoint(VMCmodel):
    """Save most recent SJA state to disk."""
    state = {
        "energy": energy,
        "precision": precision, 
        "net": VMCmodel.ansatz.state_dict()
    }
    torch.save(state, ckpt_outfile)


phys_system = PhysicalSystem(nx=Lx, ny=Ly, ns=Nsites, num_particles=Nparticles, D=space_dim, Vint=Vint)

# Aggregation of MADE neural network as Jastrow factor 
# and Slater determi
with open("HF_energy"+paramstr+".dat", "w") as fh:
    (eigvals, eigvecs) = HartreeFock_tVmodel(phys_system, potential="none", outfile=fh, max_iter=20)
np.savetxt("eigvecs.dat", eigvecs)

Sdet_sampler = SlaterDetSampler_ordered(
        Nsites=Nsites, 
        Nparticles=Nparticles, 
        single_particle_eigfunc=eigvecs, 
        eigvals=eigvals, 
        naive_update=False, 
        optimize_orbitals=optimize_orbitals
        )

SJA = SlaterJastrow_ansatz(slater_sampler=Sdet_sampler, 
        num_components=Nparticles, 
        D=Nsites, 
        net_depth=2, 
        deactivate_Jastrow=deactivate_Jastrow
        )

VMCmodel_ = VMCKernel(energy_loc=phys_system.local_energy, ansatz=SJA)
del SJA

E_exact = -3.6785841210741 #-3.86925667 # 0.4365456400025272 #-3.248988339062832 # -2.9774135797163597 #-3.3478904193465335


if True: 
    t0 = time()
    t0_tmp = t0
    for i, (energy, precision) in enumerate(train(VMCmodel_, learning_rate=0.2, learning_rate_SD=learning_rate_SD, num_samples=num_samples, num_bin=num_bin, use_cuda = use_cuda)):
        t1_tmp = time()
        print('Step %d, dE/|E| = %.4f, elapsed = %.4f' % (i, -(energy - E_exact)/E_exact, t1_tmp-t0_tmp))
        _update_curve(energy, precision)
        _checkpoint(VMCmodel_)
        t0_tmp = time()
        # stop condition
        if i >= max_iter:
            break
    t1 = time()
    print("## Timings:")
    print("## elapsed =%10.6f for %d samples per iteration with %d iterations" % (t1-t0, num_samples, max_iter))
    print("## t_psiloc=", VMCmodel_.t_psiloc)
    print("## t_logprob_B=", VMCmodel_.ansatz.t_logprob_B)
    print("## t_logprob_F=", VMCmodel_.ansatz.t_logprob_F)
    print("## t_sampling=", VMCmodel_.t_sampling)
    print("## t_locE=", VMCmodel_.t_locE)
    print("## t_backward=", VMCmodel_.t_grads)




szsz_corr = np.zeros(Nsites)
szsz_corr_2D = np.zeros((phys_system.nx, phys_system.ny))
corr_ = np.zeros(Nsites)
corr_2D_ = np.zeros((phys_system.nx, phys_system.ny))


 # ====================================================================================
print("Now sample from the converged ansatz")
###### setting up user-defined symmetry transformations for 2d lattice ######
s = np.arange(Nsites) # sites [0,1,2,....]
x = s%Lx # x positions for sites
y = s//Lx # y positions for sites
T_x = (x+1)%Lx + Lx*y # translation along x-direction
T_y = x +Lx*((y+1)%Ly) # translation along y-direction
P_x = x + Lx*(Ly-y-1) # reflection about x-axis
P_y = (Lx-x-1) + Lx*y # reflection about y-axis
#
def translate(s, n, T_d):
    """translate set of sites `s` using mapping `T_d` for n times"""
    for _ in range(n):
        s = T_d[s]
    return s


state_checkpointed = torch.load(ckpt_outfile)
VMCmodel_.ansatz.load_state_dict(state_checkpointed['net'], strict=True)
#
# init observables 
SzSzcorr = np.zeros((Lx, Ly))
nncorr = np.zeros((Lx, Ly))
#
# for calculating error bar
SzSzcorr2 = np.zeros((Lx, Ly))
nncorr2 = np.zeros((Lx, Ly))
#
energy_av = 0
energy2_av = 0
energy_list = np.zeros((num_meas_samples, 1))
#
t_sample = 0
for ii in range(num_meas_samples):
    t1 = time() 
    with torch.no_grad():
        sample_unfolded, log_prob_sample = VMCmodel_.ansatz.sample_unfolded()
    t2 = time()
    t_sample += (t2 - t1)
    config = occ_numbers_collapse(sample_unfolded, Nsites).squeeze().numpy()
    config_sz = 2*config - 1
#
    # local energy 
    ene, _ = VMCmodel_.local_measure([config], log_prob_sample)
    print("ene=", ene)
    energy_list[ii] = ene
    energy_av += ene
    energy2_av += ene**2 
#
    for tx, ty in itertools.product(range(Lx), range(Ly)):
        pair_list = [[i, translate(translate(i, tx, T_x), ty, T_y)] for i in range(Nsites)]
        ss1 = sum([config_sz[i] * config_sz[j] for (i,j) in pair_list]) / Nsites
        SzSzcorr[tx, ty] += ss1 
        ss2 = sum([config[i] * config[j] for (i,j) in pair_list]) / Nsites
        nncorr[tx, ty] += ss2 
#
        SzSzcorr2[tx, ty] += ss1**2 
        nncorr2[tx, ty] += ss2**2
#
SzSzcorr[:,:] /= num_meas_samples
nncorr[:,:] /= num_meas_samples 
SzSzcorr2[:,:] /= num_meas_samples 
nncorr2[:,:] /= num_meas_samples
# Central Limit Theorem for uncorrelated samples 
err_SzSzcorr = np.sqrt(SzSzcorr2[:,:] - SzSzcorr[:,:]**2) / np.sqrt(num_meas_samples)
err_nncorr = np.sqrt(nncorr2[:,:] - nncorr[:,:]**2) / np.sqrt(num_meas_samples)
#
np.savetxt("SzSzcorr_VMC_"+paramstr+".dat", SzSzcorr)
np.savetxt("err_SzSzcorr_VMC_"+paramstr+".dat", err_SzSzcorr)
np.savetxt("nncorr_VMC_"+paramstr+".dat", nncorr)
# Store also the connected density-density correlation function (valid only for translationally invariant systems)
##np.savetxt("nncorr_conn_VMC_"+paramstr+".dat", nncorr[:,:] - (Np / Nsites)**2 )
np.savetxt("err_nncorr_VMC_"+paramstr+".dat", err_nncorr)
#
energy_av /= num_meas_samples 
energy2_av /= num_meas_samples 
#
err_energy = np.sqrt(energy2_av - energy_av**2) / np.sqrt(num_meas_samples)
with open("energy_VMC_"+paramstr+".dat", "w") as fh:
    fh.write("energy = %16.10f +/- %16.10f" % (energy_av, err_energy))
# store timeseries => make histogram of non-Gaussian statistics 
np.savetxt("energy_TS_"+paramstr+".dat", energy_list)
#
print("## %d samples in %f seconds" % ( num_meas_samples, t_sample))