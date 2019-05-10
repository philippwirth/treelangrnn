import os
import torch

from collections import Counter

class Dictionary(object):
    def __init__(self):
        self.word2idx = {}
        self.idx2word = []
        self.counter = Counter()
        self.total = 0

    def add_word(self, word):
        if word not in self.word2idx:
            self.idx2word.append(word)
            self.word2idx[word] = len(self.idx2word) - 1
        token_id = self.word2idx[word]
        self.counter[token_id] += 1
        self.total += 1
        return self.word2idx[word]

    def __len__(self):
        return len(self.idx2word)


class Corpus(object):
    def __init__(self, path, is_sorted):
        self.frequencies = None
        self.resets, self.reset_idxs = ['<eos>'], set()
        self.nsentences_of_length = None

        self.dictionary = Dictionary()
        if is_sorted:
            self.nsentences_of_length = dict()
            self.train = self.tokenize_sorted(os.path.join(path, 'train.txt'))
        else:
            self.train = self.tokenize(os.path.join(path, 'train.txt'), True)
        
        self.valid = self.tokenize(os.path.join(path, 'valid.txt'))
        self.test = self.tokenize(os.path.join(path, 'test.txt'))


    def tokenize(self, path, first=True):
        """Tokenizes a text file."""
        assert os.path.exists(path)
        # Add words to the dictionary
        with open(path, 'r') as f:
            tokens = 0
            for line in f:
                words = line.split() + ['<eos>']
                tokens += len(words)
                for word in words:
                    self.dictionary.add_word(word)

        # initialize frequencies
        if first:
            self.frequencies = torch.zeros(len(list(self.dictionary.counter)))
            for (token_id, freq) in self.dictionary.counter.most_common():
                self.frequencies[token_id] = freq

        # Tokenize file content
        with open(path, 'r') as f:
            ids = torch.LongTensor(tokens)
            token = 0
            for line in f:
                words = line.split() + ['<eos>']
                for word in words:
                    
                    # store tokens which signal end of sentence
                    if word in self.resets:
                        self.reset_idxs.add(self.dictionary.word2idx[word])

                    ids[token] = self.dictionary.word2idx[word]

                    token += 1

        return ids
    def tokenize_sorted(self, path):
        """Tokenizes a text file."""
        assert os.path.exists(path)
        # Add words to the dictionary
        max_sentence_length = 0
        with open(path, 'r') as f:
            tokens = 0
            for line in f:
                words = line.split() + ['<eos>']
                tokens += len(words)
                for word in words:
                    self.dictionary.add_word(word)

                # update number of sentences with current length
                if max_sentence_length < len(words):
                    max_sentence_length = len(words)
                    self.nsentences_of_length[max_sentence_length] = 1
                else:
                    self.nsentences_of_length[len(words)] += 1

        # initialize frequencies
        self.frequencies = torch.zeros(len(list(self.dictionary.counter)))
        for (token_id, freq) in self.dictionary.counter.most_common():
            self.frequencies[token_id] = freq

        # Tokenize file content
        with open(path, 'r') as f:
            ids = torch.LongTensor(tokens)
            token = 0
            for line in f:
                words = line.split() + ['<eos>']
                for word in words:
                    
                    # store tokens which signal end of sentence
                    if word in self.resets:
                        self.reset_idxs.add(self.dictionary.word2idx[word])

                    ids[token] = self.dictionary.word2idx[word]

                    token += 1

        return ids


def sort_dataset(in_path, out_path):

    assert os.path.exists(in_path)
    # Add words to the dictionary
    with open(in_path, 'r') as f:
        lines = [line.split() for line in f]
        
    lines.sort(key=len)
    lines = [' '.join(line) for line in lines]
    print(lines)

    with open(out_path, 'w') as f:
        f.writelines(lines)
        




sort_dataset('data/penn/test.txt', 'data/penn_sorted/test.txt')






