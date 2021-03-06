import torch
import torch.nn as nn
import numpy as np

from embed_regularize import embedded_dropout
from locked_dropout import LockedDropout
from weight_drop import WeightDrop

from sample import NegativeSampler

from utils import repackage_hidden

from distance import eucl_distance, dot_distance, cone_distance
from hb_helpers import pairwise_poinc_distance
from activation import log_softmax, log_sigmoid

class RNNModel(nn.Module):
    """Container module with an encoder and a recurrent module."""

    def __init__(self, ntoken, ninp, nhid, dropout=0.5, dropouth=0.5, dropouti=0.5, dropoute=0.1, wdrop=0.5,
                nsamples=10, temperature=65, frequencies=None, bias=True, bias_reg=1., dist_fn='eucl',
                activation_fn='logsoftmax'):

        super(RNNModel, self).__init__()
        self.lockdrop = LockedDropout()
        self.idrop = nn.Dropout(dropouti)
        self.hdrop = nn.Dropout(dropouth)
        self.drop = nn.Dropout(dropout)
        self.encoder = nn.Embedding(ntoken, ninp)

        self.rnn = torch.nn.RNN(ninp, nhid, 1, dropout=0)
        self.rnn = WeightDrop(self.rnn, ['weight_hh_l0'], dropout=wdrop)
        print(self.rnn)

        # initialize bias
        self.bias_reg = bias_reg
        if bias:
            self.decoder = nn.Linear(nhid, ntoken)
            self.bias = self.decoder.bias
        else:
            self.bias = None
        self.init_weights(bias)

        # store input arguments
        self.ninp = ninp
        self.nhid = nhid
        self.dropout = dropout
        self.dropouti = dropouti
        self.dropouth = dropouth
        self.dropoute = dropoute
        self.wdrop = wdrop

        # nonlinearity needs to be the same as for RNN!
        self.nonlinearity = nn.Tanh()
        self.nsamples = nsamples
        self.temp = temperature
        self.ntoken = ntoken

        self.sampler = NegativeSampler(self.nsamples, torch.ones(self.ntoken) if frequencies is None else frequencies)

        # set activation
        if activation_fn == 'logsoftmax':
            self.activation = log_softmax
        elif activation_fn == 'logsigmoid':
            self.activation = log_sigmoid
        else:
            self.activation = None

        # set distance function
        if dist_fn == 'eucl':
            self.dist_fn = eucl_distance
        elif dist_fn == 'dot':
            self.dist_fn = dot_distance
        elif dist_fn == 'poinc':
            self.dist_fn = pairwise_poinc_distance
        else:
            self.dist_fn = cone_distance
        

    def init_weights(self, bias):
        initrange = .1
        self.encoder.weight.data.uniform_(-initrange, initrange)
        if bias: self.decoder.weight.data.uniform_(-initrange, initrange)

    def forward(self, data, hidden, return_output=False):

        # get batch size and sequence length
        seq_len, bsz = data.size()

        emb = embedded_dropout(self.encoder, data, dropout=self.dropoute if self.training else 0)
        emb = self.lockdrop(emb, self.dropouti)

        raw_output, new_hidden = self.rnn(emb, hidden)          # apply single layer rnn
        raw_output = self.lockdrop(raw_output, self.dropout)    # seq_len x bsz x nhid
        raw_output = raw_output.view(seq_len, bsz, -1)          # reshape for concat
        raw_output = torch.cat((hidden, raw_output), 0)         # concatenate initial hidden state

        # initialize loss w/ positive terms
        pos_sample_distances = [self.temp * self.dist_fn(raw_output[i], raw_output[i+1], None if self.bias is None else self.bias[data[i]]) for i in range(seq_len)]

        new_hidden = raw_output[-1].view(1, bsz, -1)            # new hidden is last output
        raw_output = raw_output[:-1].view(seq_len*bsz, -1)      # hiddens used for negative sampling are all except last

        # x stores the positive samples at index 0 and the negative ones a 1:nsamples+1
        x = torch.zeros(1+self.nsamples, seq_len*bsz).cuda()
        for i in range(seq_len):
            x[0][i*bsz:(i+1)*bsz] = pos_sample_distances[i]
        
        # process negative samples
        samples = self.sampler(bsz, seq_len)    # (nsamples x bsz x seq_len)
        samples_emb = embedded_dropout(self.encoder, samples, dropout=self.dropoute if self.training else 0)
        samples_emb = self.lockdrop(samples_emb, self.dropouti)

        # only one layer for the moment
        weights_ih, bias_ih = self.rnn.module.weight_ih_l0, self.rnn.module.bias_ih_l0  
        weights_hh, bias_hh = self.rnn.module.weight_hh_l0, self.rnn.module.bias_hh_l0

        # reshape samples for indexing and precompute the inputs to nonlinearity
        samples = samples.view(self.nsamples, bsz*seq_len)
        samples_times_W = torch.nn.functional.linear(samples_emb, weights_ih, bias_ih).view(self.nsamples, bsz*seq_len, -1)
        hiddens_times_U = torch.nn.functional.linear(raw_output, weights_hh, bias_hh)
        
        # iterate over samples to update loss
        for i in range(self.nsamples):

            # compute output of negative samples
            output = self.nonlinearity(samples_times_W[i] + hiddens_times_U)
            output = self.lockdrop(output.view(1, output.size(0), -1), self.dropout)
            output = output[0]

            # compute loss term
            #bias = None
            #distance = self.dist_fn(raw_output, output, None)
            distance = self.dist_fn(raw_output, output, None if self.bias is None else self.bias[samples[i]])
            #distance = torch.clamp(distance, max=self.clamp)
            x[i+1] = self.temp * distance

        loss = self.activation(x)
        if self.bias_reg > 0: loss = loss + (0 if self.bias is None else self.bias_reg * torch.norm(self.bias).pow(2))

        return loss, new_hidden


    def evaluate(self, data, eos_tokens=None, dump_hiddens=False):

        # get weights and compute WX for all words
        weights_ih, bias_ih = self.rnn.module.weight_ih_l0, self.rnn.module.bias_ih_l0  # only one layer for the moment
        weights_hh, bias_hh = self.rnn.module.weight_hh_l0, self.rnn.module.bias_hh_l0

        all_words = torch.LongTensor([i for i in range(self.ntoken)]).cuda()
        all_words = embedded_dropout(self.encoder, all_words, dropout=self.dropoute if self.training else 0)

        all_words_times_W = torch.nn.functional.linear(all_words, weights_ih, bias_ih)

        # iterate over data set and compute loss
        total_loss, hidden = 0, self.init_hidden(1)
        i = 0

        entropy, hiddens, all_hiddens = [], [], []
        while i < data.size(0):

            hidden_times_U = torch.nn.functional.linear(hidden[0].repeat(self.ntoken, 1), weights_hh, bias_hh)
            output = self.nonlinearity(all_words_times_W + hidden_times_U)

            if dump_hiddens: hiddens.append(output[data[i]].data.cpu().numpy())

            distance = self.dist_fn(hidden[0], output, self.bias)
            softmaxed = torch.nn.functional.log_softmax(self.temp * distance.view(-1), dim=0)
            raw_loss = -softmaxed[data[i]].item()

            total_loss += raw_loss / data.size(0)
            entropy.append(raw_loss)

            if not eos_tokens is None and data[i].data.cpu().numpy()[0] in eos_tokens:
                hidden = self.init_hidden(1)
                if dump_hiddens:
                    all_hiddens.append(hiddens)
                    hiddens = []
            else:
                hidden = output[data[i]].view(1, 1, -1)
            hidden = repackage_hidden(hidden)

            i = i + 1

        all_hiddens = all_hiddens if not eos_tokens is None else hiddens
        
        if dump_hiddens:
            return total_loss, np.array(entropy), all_hiddens
        else:
            return total_loss, np.array(entropy)


    def init_hidden(self, bsz):
        weight = next(self.parameters()).data
        return weight.new(1, bsz, self.nhid).zero_()
