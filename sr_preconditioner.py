from sr_lazy import SR_Preconditioner_base
import torch 
import numpy as np

from scipy.sparse.linalg import LinearOperator, cg 

class SR_Preconditioner(SR_Preconditioner_base):
    """This subclass provides the interface to gradients computed in torch."""
    def __init__(self, num_params, num_samples, diag_shift=0.0, lazy=True, dtype=np.float64):
        super(SR_Preconditioner, self).__init__(num_params, num_samples, diag_shift=diag_shift, lazy=lazy, dtype=dtype)

    def _flatten(self, grad):
        """The gradient (and also O_k)  is a list of torch tensor matrices and vectors (same as module.parameters()).
        Flatten it into a single continuous vector. Input `grad`is a numpy array. """
        grad_flat = np.hstack([p.flatten() for p in grad])
        return grad_flat 

    def _unflatten(self, grad_flat, grad):
        """Use information in `grad` to reassemble flattened gradient into blocks of parameters."""
        offset = 0
        grad_new = []
        for ps in [list(p.size()) for p in grad]:
            param_block = grad_flat[offset:offset+np.prod(ps)].reshape(ps)
            grad_new.append(torch.tensor(param_block))
            offset += np.prod(ps)
        return grad_new  
        
    def accumulate(self, O_k):
       super(SR_Preconditioner, self).accumulate(self._flatten(O_k))

    def apply_Sinv(self, g, tol=1e-8):
        # # test condition number of S-matrix 
        # S = self._to_dense()        
        # diagS = np.diag(S)
        # # sort  
        # perm =  np.argsort(diagS)
        # # apply S^{-1} only to vector elements larger than `thresh` 
        # lidx = np.searchsorted(diagS[perm], thresh, side='right')
        # S_sorted = S[np.ix_(perm[lidx:], perm[lidx:])]

        # print("condition number=", np.linalg.cond(S_sorted))

        # g_flat = self._flatten(g)
        # g_flat_sorted = g_flat[perm]

        # g2_part_flat = np.linalg.inv(S_sorted) @ g_flat_sorted[lidx:]        
        # g2_flat = g_flat 
        # g2_flat[lidx:] = g2_part_flat[:]
        # # sort back 
        # invperm = np.argsort(perm)
        # g2_flat = g2_flat[invperm]
        # print("condition number=", np.linalg.cond(S_sorted))
        # self.reset()
        # return self._unflatten(g, g2_flat)

        ############################
        g_flat = self._flatten(g)
        x = super(SR_Preconditioner, self).apply_Sinv(g_flat, tol=tol)
        # we don't need the Fisher information matrix any longer
        self.reset()
        return self._unflatten(x, g)
        ############################
        

     