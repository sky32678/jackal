#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
    ANFIS in torch: the ANFIS layers
    @author: James Power <james.power@mu.ie> Apr 12 18:13:10 2019
    Acknowledgement: twmeggs' implementation of ANFIS in Python was very
    useful in understanding how the ANFIS structures could be interpreted:
        https://github.com/twmeggs/anfis
'''

import itertools
from collections import OrderedDict

import numpy as np

import torch
import torch.nn.functional as F
from random import sample


from consequent_layer import MamdaniConsequentLayer

dtype = torch.float


class FuzzifyVariable(torch.nn.Module):
    '''
        Represents a single fuzzy variable, holds a list of its MFs.
        Forward pass will then fuzzify the input (value for each MF).
    '''
    def __init__(self, mfdefs):
        super(FuzzifyVariable, self).__init__()
        if isinstance(mfdefs, list):  # No MF names supplied
            mfnames = ['mf{}'.format(i) for i in range(len(mfdefs))]
            mfdefs = OrderedDict(zip(mfnames, mfdefs))
        self.mfdefs = torch.nn.ModuleDict(mfdefs)
        self.padding = 0

    @property
    def num_mfs(self):
        '''Return the actual number of MFs (ignoring any padding)'''
        return len(self.mfdefs)

    def members(self):
        '''
            Return an iterator over this variables's membership functions.
            Yields tuples of the form (mf-name, MembFunc-object)
        '''
        return self.mfdefs.items()

    def pad_to(self, new_size):
        '''
            Will pad result of forward-pass (with zeros) so it has new_size,
            i.e. as if it had new_size MFs.
        '''
        self.padding = new_size - len(self.mfdefs)

    def fuzzify(self, x):
        '''
            Yield a list of (mf-name, fuzzy values) for these input values.
        '''
        for mfname, mfdef in self.mfdefs.items():
            yvals = mfdef(x)
            yield(mfname, yvals)

    def forward(self, x):
        '''
            Return a tensor giving the membership value for each MF.
            x.shape: n_cases
            y.shape: n_cases * n_mfs
        '''
        y_pred = torch.cat([mf(x) for mf in self.mfdefs.values()], dim=1)
        if self.padding > 0:
            y_pred = torch.cat([y_pred,
                                torch.zeros(x.shape[0], self.padding)], dim=1)

        return y_pred


class FuzzifyLayer(torch.nn.Module):
    '''
        A list of fuzzy variables, representing the inputs to the FIS.
        Forward pass will fuzzify each variable individually.
        We pad the variables so they all seem to have the same number of MFs,
        as this allows us to put all results in the same tensor.
    '''
    def __init__(self, varmfs, varnames=None):
        super(FuzzifyLayer, self).__init__()
        if not varnames:
            self.varnames = ['x{}'.format(i) for i in range(len(varmfs))]
        else:
            self.varnames = list(varnames)
        maxmfs = max([var.num_mfs for var in varmfs])
        for var in varmfs:
            var.pad_to(maxmfs)
        self.varmfs = torch.nn.ModuleDict(zip(self.varnames, varmfs))

    @property
    def num_in(self):
        '''Return the number of input variables'''
        return len(self.varmfs)

    @property
    def max_mfs(self):
        ''' Return the max number of MFs in any variable'''
        return max([var.num_mfs for var in self.varmfs.values()])

    def __repr__(self):
        '''
            Print the variables, MFS and their parameters (for info only)
        '''
        r = ['Input variables']
        for varname, members in self.varmfs.items():
            r.append('Variable {}'.format(varname))
            for mfname, mfdef in members.mfdefs.items():
                r.append('- {}: {}({})'.format(mfname,
                         mfdef.__class__.__name__,
                         ', '.join(['{}={}'.format(n, p.item())
                                   for n, p in mfdef.named_parameters()])))
        return '\n'.join(r)

    def forward(self, x):
        ''' Fuzzyify each variable's value using each of its corresponding mfs.
            x.shape = n_cases * n_in
            y.shape = n_cases * n_in * n_mfs
        '''

        assert x.shape[1] == self.num_in,\
            '{} is wrong no. of input values'.format(self.num_in)
        y_pred = torch.stack([var(x[:, i:i+1])
                              for i, var in enumerate(self.varmfs.values())],
                             dim=1)
        return y_pred


class AntecedentLayer(torch.nn.Module):
    '''
        Form the 'rules' by taking all possible combinations of the MFs
        for each variable. Forward pass then calculates the fire-strengths.
    '''
    def __init__(self, varlist):
        super(AntecedentLayer, self).__init__()
        # Count the (actual) mfs for each variable:
        mf_count = [var.num_mfs for var in varlist]
        # Now make the MF indices for each rule:

        #######full combinations of rule bases
        mf_indices = itertools.product(*[range(n) for n in mf_count])
    #    print(*mf_indices)
        #I can reduce the rules at here but still have some problems
    #    mf_indices = list(mf_indices)
    #    mf_indices = sample(mf_indices, 35)
    #    mf_indices[0] = (0,0,0,0,3)
    #    print(mf_indices)
        mf_indices = [(0,0,5),(0,1,5),(0,2,5),(0,3,5),(0,4,5),
                      (1,5,0),(1,5,1),(1,5,2),(1,5,3),(1,5,4),
                      (2,5,0),(2,5,1),(2,5,2),(2,5,3),(2,5,4),
                      (3,5,0),(3,5,1),(3,5,2),(3,5,3),(0,5,4),
                      (4,0,5),(4,1,5),(4,2,5),(4,3,5),(4,4,5),]
    #    self.mf_indices = torch.tensor((mf_indices))

        ########popping one rule base

        ##We can pick our rule bases manually where
        ##For example , (0, 0 ,1) , (1, 2, 2), (2,1,0) .....etc
        # 0 = 'far_left'
        # 1 = 'near_left'
        # 2 = 'zero'
        # 3 = 'near_right'
        # 4 = 'far_right'
        # 5 = 'none'
        mf_out = [-3,-2,0,2,3,
                  -3,-2,-1,0,1,
                  -2,-1,0,1,2,
                  -1,0,1,2,3,
                  3,2,0,-2,-3]
        # outputs_membership = [
        #         (6,),  # 1
        #         (5,),  # 2
        #         (3,),  # 3
        #         (1,),  # 4
        #         (0,),  # 5
        #         (0,),  # 6
        #         (1,),  # 7
        #         (3,),  # 8
        #         (5,),  # 9
        #         (6,),  # 10
        #         (6,),  # 11
        #         (5,),  # 12
        #         (4,),  # 13
        #         (3,),  # 14
        #         (2,),  # 15
        #         (5,),  # 16
        #         (4,),  # 17
        #         (3,),  # 18
        #         (2,),  # 19
        #         (1,),  # 20
        #         (4,),  # 21
        #         (3,),  # 22
        #         (2,),  # 23
        #         (1,),  # 24
        #         (0,),  # 25
        #     ]

        self.mf_indices = torch.tensor(list(mf_indices))
        self.mf_out = mf_out
        # mf_indices.shape is n_rules * n_in
        print(mf_count)
    #    print(list(mf_indices))
        print(len(self.mf_indices))

    def num_rules(self):
        return len(self.mf_indices)

    def extra_repr(self, varlist=None):
        if not varlist:
            return None
        row_ants = []
        mf_count = [len(fv.mfdefs) for fv in varlist.values()]
        for rule_idx in itertools.product(*[range(n) for n in mf_count]):
            thisrule = []
            for (varname, fv), i in zip(varlist.items(), rule_idx):
                thisrule.append('{} is {}'
                                .format(varname, list(fv.mfdefs.keys())[i]))
            row_ants.append(' and '.join(thisrule))
        return '\n'.join(row_ants)

    def forward(self, x):
        ''' Calculate the fire-strength for (the antecedent of) each rule
            x.shape = n_cases * n_in * n_mfs
            y.shape = n_cases * n_rules
        '''
        # Expand (repeat) the rule indices to equal the batch size:
        batch_indices = self.mf_indices.expand((x.shape[0], -1, -1))
        # Then use these indices to populate the rule-antecedents
        ants = torch.gather(x.transpose(1, 2), 1, batch_indices)
        # ants.shape is n_cases * n_rules * n_in
        # Last, take the AND (= product) for each rule-antecedent
        rules = torch.prod(ants, dim=2)
    #    print(rules[0])
        return rules


class ConsequentLayer(torch.nn.Module):

    def __init__(self, d_in, d_rule, d_out):
        super(ConsequentLayer, self).__init__()
        c_shape = torch.Size([d_rule, d_out, d_in + 1])
    #    c_shape = torch.Size([d_rule, d_out, 1])
        self._coeff = torch.zeros(c_shape, dtype=dtype, requires_grad=True)
    #    self._coeff = self._coeff.transpose(0,2)
    #    print(self._coeff)
    #    y_pred.transpose(0, 2)
    #    dsads
    @property
    def coeff(self):
        '''
            Record the (current) coefficients for all the rules
            coeff.shape: n_rules * n_out * (n_in+1)
        '''
        return self._coeff

    @coeff.setter
    def coeff(self, new_coeff):
        '''
            Record new coefficients for all the rules
            coeff: for each rule, for each output variable:
                   a coefficient for each input variable, plus a constant
        '''
        assert new_coeff.shape == self.coeff.shape, \
            'Coeff shape should be {}, but is actually {}'\
            .format(self.coeff.shape, new_coeff.shape)
        self._coeff = new_coeff

    def fit_coeff(self, x, weights, y_actual):
        '''
            Use LSE to solve for coeff: y_actual = coeff * (weighted)x
                  x.shape: n_cases * n_in
            weights.shape: n_cases * n_rules
            [ coeff.shape: n_rules * n_out * (n_in+1) ]
                  y.shape: n_cases * n_out
        '''
        # Append 1 to each list of input vals, for the constant term:
        x_plus = torch.cat([x, torch.ones(x.shape[0], 1)], dim=1)
        # Shape of weighted_x is n_cases * n_rules * (n_in+1)
        weighted_x = torch.einsum('bp, bq -> bpq', weights, x_plus)
        # Can't have value 0 for weights, or LSE won't work:
        weighted_x[weighted_x == 0] = 1e-12
        # Squash x and y down to 2D matrices for lstsq:
        weighted_x_2d = weighted_x.view(weighted_x.shape[0], -1)
        y_actual_2d = y_actual.view(y_actual.shape[0], -1)
        # Use  to do LSE, then pick out the solution rows:
        try:
            coeff_2d, _ = torch.lstsq(y_actual_2d, weighted_x_2d)
        except RuntimeError as e:
            print('Internal error in lstsq', e)
            print('Weights are:', weighted_x)
            raise e
        coeff_2d = coeff_2d[0:weighted_x_2d.shape[1]]
        # Reshape to 3D tensor: divide by rules, n_in+1, then swap last 2 dims
        self.coeff = coeff_2d.view(weights.shape[1], x.shape[1]+1, -1)\
            .transpose(1, 2)
        # coeff dim is thus: n_rules * n_out * (n_in+1)

    def forward(self, x):
        '''
            Calculate: y = coeff * x + const   [NB: no weights yet]
                  x.shape: n_cases * n_in
              coeff.shape: n_rules * n_out * (n_in+1)
                  y.shape: n_cases * n_out * n_rules
        '''
        # Append 1 to each list of input vals, for the constant term:
        x_plus = torch.cat([x, torch.ones(x.shape[0], 1)], dim=1)
        # Need to switch dimansion for the multipy, then switch back:
        y_pred = torch.matmul(self.coeff, x_plus.t())
    #    print(self.coeff)
    #    dsads
        return y_pred.transpose(0, 2)  # swaps cases and rules


class PlainConsequentLayer(ConsequentLayer):
    '''
        A linear layer to represent the TSK consequents.
        Not hybrid learning, so coefficients are backprop-learnable parameters.
    '''
    def __init__(self, *params):
        super(PlainConsequentLayer, self).__init__(*params)
    #    self.register_parameter('coefficients',
    #                            torch.nn.Parameter(self._coeff))
    #    print(len(self._coeff))


    @property
    def coeff(self):
        '''
            Record the (current) coefficients for all the rules
            coeff.shape: n_rules * n_out * (n_in+1)
        '''
        return self.coefficients

    def fit_coeff(self, x, weights, y_actual):
        '''
        '''
        assert False,\
            'Not hybrid learning: I\'m using BP to learn coefficients'


class WeightedSumLayer(torch.nn.Module):

    def __init__(self):
        super(WeightedSumLayer, self).__init__()

    def forward(self, weights, tsk):
        '''
            weights.shape: n_cases * n_rules
                tsk.shape: n_cases * n_out * n_rules
             y_pred.shape: n_cases * n_out
        '''
        # Add a dimension to weights to get the bmm to work:
        y_pred = torch.bmm(tsk, weights.unsqueeze(2))
        return y_pred.squeeze(2)

class ProductSum(torch.nn.Module):
    def forward(self, weights, tsk):
        return torch.matmul(weights, tsk)

class AnfisNet(torch.nn.Module):

    def __init__(self, description, invardefs, outvarnames, mamdani_out, input_keywords, number_of_mfs, hybrid=True):
        super(AnfisNet, self).__init__()
        self.description = description
        self.outvarnames = outvarnames
        self.hybrid = hybrid
        varnames = [v for v, _ in invardefs]
        mfdefs = [FuzzifyVariable(mfs) for _, mfs in invardefs]
        self.num_in = len(invardefs)
        self.input_keywords = input_keywords
        self.number_of_mfs = number_of_mfs
        #######setting number of rule base for anfis structure
        self.num_rules = np.prod([len(mfs) for _, mfs in invardefs]) ##full comb
        self.num_rules = 25
        ###############################################################
        #print(mfdefs[0]['()'])
        print(self.num_rules)
        # self.mam_varnames = [mam_v for mam_v, _ in mamdani_out]
        #print(mam_varnames)
        # self.mam_mfdefs = [FuzzifyVariable(mam_mfs) for _, mam_mfs in mamdani_out]
        mf_out = [-3,-2,0,2,3,
                  -3,-2,-1,0,1,
                  -2,-1,0,1,2,
                  -1,0,1,2,3,
                  3,2,0,-2,-3]
        # self.names = {
        #     0: 'Hard Left',
        #     1: 'Left',
        #     2: 'Soft Left',
        #     3: 'Zero',
        #     4: 'Soft Right',
        #     5: 'Right',
        #     6: 'Hard Right',
        # }
        mf_out = [
                (6,),  # 1
                (5,),  # 2
                (3,),  # 3
                (1,),  # 4
                (0,),  # 5
                (6,),  # 6
                (5,),  # 7
                (4,),  # 8
                (3,),  # 9
                (2,),  # 10
                (5,),  # 11
                (4,),  # 12
                (3,),  # 13
                (2,),  # 14
                (1,),  # 15
                (4,),  # 16
                (3,),  # 17
                (2,),  # 18
                (1,),  # 19
                (0,),  # 20
                (0,),  # 21
                (1,),  # 22
                (3,),  # 23
                (5,),  # 24
                (6,),  # 25
            ]

        # mf_out = [
        #         (6,),  # 1
        #         (5,),  # 2
        #         (3,),  # 3
        #         (1,),  # 4
        #         (0,),  # 5
        #         (0,),  # 6
        #         (1,),  # 7
        #         (3,),  # 8
        #         (5,),  # 9
        #         (6,),  # 10
        #         (6,),  # 11
        #         (5,),  # 12
        #         (4,),  # 13
        #         (3,),  # 14
        #         (2,),  # 15
        #         (5,),  # 16
        #         (4,),  # 17
        #         (3,),  # 18
        #         (2,),  # 19
        #         (1,),  # 20
        #         (4,),  # 21
        #         (3,),  # 22
        #         (2,),  # 23
        #         (1,),  # 24
        #         (0,),  # 25
        #     ]

        if self.hybrid:
            cl = ConsequentLayer(self.num_in, self.num_rules, self.num_out)
        else:
            cl = MamdaniConsequentLayer(mamdani_out, mf_out)

        output = ProductSum()
        self.layer = torch.nn.ModuleDict(OrderedDict([
            ('fuzzify', FuzzifyLayer(mfdefs, varnames)),
            ('rules', AntecedentLayer(mfdefs)),
            # normalisation layer is just implemented as a function.
            ('consequent', cl),
            ('output', output),
            # weighted-sum layer is just implemented as a function.
            ]))

    @property
    def num_out(self):
        return len(self.outvarnames)

    @property
    def coeff(self):
        return self.layer['consequent'].coeff

    @coeff.setter
    def coeff(self, new_coeff):
        self.layer['consequent'].coeff = new_coeff

    def fit_coeff(self, x, y_actual):
        '''
            Do a forward pass (to get weights), then fit to y_actual.
            Does nothing for a non-hybrid ANFIS, so we have same interface.
        '''
        if self.hybrid:
            self(x)
            self.layer['consequent'].fit_coeff(x, self.weights, y_actual)

    def input_variables(self):
        '''
            Return an iterator over this system's input variables.
            Yields tuples of the form (var-name, FuzzifyVariable-object)
        '''
        return self.layer['fuzzify'].varmfs.items()

    def output_variables(self):
        '''
            Return an list of the names of the system's output variables.
        '''
        return self.outvarnames

    def extra_repr(self):
        rstr = []
        vardefs = self.layer['fuzzify'].varmfs
        rule_ants = self.layer['rules'].extra_repr(vardefs).split('\n')
        for i, crow in enumerate(self.layer['consequent'].coeff):
            rstr.append('Rule {:2d}: IF {}'.format(i, rule_ants[i]))
            rstr.append(' '*9+'THEN {}'.format(crow.tolist()))
        return '\n'.join(rstr)

    def forward(self, x):
        '''
            Forward pass: run x thru the five layers and return the y values.
            I save the outputs from each layer to an instance variable,
            as this might be useful for comprehension/debugging.
        '''
        self.fuzzified = self.layer['fuzzify'](x)
        self.raw_weights = self.layer['rules'](self.fuzzified)
        self.weights = F.normalize(self.raw_weights, p=1, dim=1)
        self.rule_tsk = self.layer['consequent'](x)
        self.y_pred = self.layer['output'](self.weights, self.rule_tsk)

        return self.y_pred