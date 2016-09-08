import pandas as pd
import numpy as np
import tfnn
from tfnn.body.layer import Layer
from tfnn.datasets.normalizer import Normalizer


class Network(object):
    def __init__(self, input_size, output_size, do_dropout, do_l2, ntype):
        self.normalizer = Normalizer()
        self.input_size = input_size
        self.output_size = output_size
        if do_dropout and do_l2:
            raise ValueError('Cannot do dropout and l2 at once. Choose only one of them.')
        if do_dropout:
            self.reg = 'dropout'
        if do_l2:
            self.reg = 'l2'
        if (do_dropout is False) & (do_l2 is False):
            self.reg = None

        with tfnn.name_scope('inputs'):
            self.data_placeholder = tfnn.placeholder(dtype=tfnn.float32, shape=[None, self.input_size], name='x_input')
            self.target_placeholder = tfnn.placeholder(dtype=tfnn.float32, shape=[None, self.output_size], name='y_input')
            if do_dropout:
                self.keep_prob_placeholder = tfnn.placeholder(dtype=tfnn.float32)
                tfnn.scalar_summary('dropout_keep_probability', self.keep_prob_placeholder)
                _para = {'reg': self.reg, 'keep_prob': self.keep_prob_placeholder}
            elif do_l2:
                self.l2_placeholder = tfnn.placeholder(tfnn.float32)
                tfnn.scalar_summary('l2_lambda', self.l2_placeholder)
                _para = {'reg': self.reg, 'l2_lambda': self.l2_placeholder}
            else:
                _para = {'reg': self.reg}
        _para['ntype'] = ntype
        _input_layer_configs = \
            {'type': ['input'],
             'name': ['input_layer'],
             'neural_structure': [{'input_size': self.input_size, 'output_size': self.input_size}],
             'para': [_para],
             'net_in_out': [{'input_size': self.input_size, 'output_size': self.output_size}]}
        _input_layer_results = \
            {'Layer': [None],
             'Wx_plus_b': [None],
             'activated': [None],
             'dropped': [None],
             'final': [self.data_placeholder]}

        self.layers_configs = pd.DataFrame(_input_layer_configs)
        self.layers_results = pd.DataFrame(_input_layer_results)
        self.layers = []

    def build_layers(self, layers):
        if isinstance(layers, Layer):
            layers.construct(self.layers_configs, self.layers_results)
            self._add_to_log(layers)
            if layers.layer_type == 'output':
                self._init_loss()
        elif isinstance(layers, (list, tuple)):
            for layer in layers:
                layer.construct(self.layers_configs, self.layers_results)
                self._add_to_log(layer)
                if layer.layer_type == 'output':
                    self._init_loss()
        else:
            raise ValueError('layers must be a list of layer objects, or a single layer object. '
                             'Not a %s' % type(layers))

    def add_hidden_layer(self, n_neurons, activator=None, dropout_layer=False,
                         w_initial='xavier', name=None,):
        """
        For original or simple neural network.
        """
        _layer = tfnn.HiddenLayer(n_neurons, activator, dropout_layer,
                                  w_initial, name)
        _layer.construct(self.layers_configs, self.layers_results)
        self._add_to_log(_layer)

    def add_fc_layer(self, n_neurons, activator=None, dropout_layer=False,
                     w_initial='xavier', name=None):
        _layer = tfnn.FCLayer(n_neurons, activator, dropout_layer,
                              w_initial, name)
        _layer.construct(self.layers_configs, self.layers_results)
        self._add_to_log(_layer)

    def add_conv_layer(self,
                       patch_x, patch_y, n_filters, activator=None,
                       strides=(1, 1), padding='SAME',
                       pooling='max', pool_strides=(2, 2), pool_k=(2, 2),
                       pool_padding='SAME', image_shape=None,
                       dropout_layer=False, w_initial='xavier', name=None,
                       ):
        _layer = tfnn.ConvLayer(
            patch_x, patch_y, n_filters, activator,
            strides, padding, pooling, pool_strides, pool_k,
            pool_padding, image_shape,
            dropout_layer, w_initial, name)
        _layer.construct(self.layers_configs, self.layers_results)
        self._add_to_log(_layer)

    def add_output_layer(self, activator=None, dropout_layer=False,
                         w_initial='xavier', name=None,):
        _layer = tfnn.OutputLayer(activator, dropout_layer,
                                  w_initial, name)
        _layer.construct(self.layers_configs, self.layers_results)
        self._add_to_log(_layer)
        self._init_loss()

    def set_optimizer(self, optimizer=None, global_step=None,):
        if optimizer is None:
            self._lr = 0.001
            optimizer = tfnn.train.GradientDescentOptimizer(self._lr)
        if self.layers_configs['type'].iloc[-1] != 'output':
            raise NotImplementedError('Please add output layer.')
        with tfnn.name_scope('trian'):
            if hasattr(optimizer, '_lr'):
                self._lr = optimizer._lr
            elif hasattr(optimizer, '_learning_rate'):
                self._lr = optimizer._learning_rate
            else:
                raise AttributeError('this optimizer %s dose not have _lr ot _learning rate'
                                     % optimizer._name)
            tfnn.scalar_summary('learning_rate', self._lr)
            self._train_op = optimizer.minimize(self.loss, global_step, name='train_op')
        self.sess = tfnn.Session()

    def run_step(self, feed_xs, feed_ys, keep_prob=None, l2=None):
        if np.ndim(feed_xs) == 1:
            feed_xs = feed_xs[np.newaxis, :]
        if np.ndim(feed_ys) == 1:
            feed_ys = feed_ys[np.newaxis, :]
        if not hasattr(self, '_init'):
            # initialize all variables
            self._init = tfnn.initialize_all_variables()
            self.sess.run(self._init)

        if self.reg == 'dropout':
            if keep_prob is None:
                raise ValueError('need pass a keep_prob for run_step')
            self.sess.run(self._train_op, feed_dict={
                self.data_placeholder: feed_xs,
                self.target_placeholder: feed_ys,
                self.keep_prob_placeholder: keep_prob})
        elif self.reg == 'l2':
            if l2 is None:
                raise ValueError('need pass a l2 for run_step')
            self.sess.run(self._train_op, feed_dict={
                self.data_placeholder: feed_xs,
                self.target_placeholder: feed_ys,
                self.l2_placeholder: l2})
        else:
            self.sess.run(self._train_op, feed_dict={
                self.data_placeholder: feed_xs,
                self.target_placeholder: feed_ys})

    def fit(self, feed_xs, feed_ys, steps=2000, *args):
        """
        Fit data to network, automatically training the network.
        :param feed_xs:
        :param feed_ys:
        :param steps: when n_iter=-1, the training steps= n_samples*2
        :param args: pass keep_prob when use dropout, pass l2_lambda when use l2 regularization.
        :return: Nothing
        """
        train_data = tfnn.Data(feed_xs, feed_ys)
        for _ in range(steps):
            b_xs, b_ys = train_data.next_batch(100, loop=True)
            self.run_step(feed_xs=b_xs, feed_ys=b_ys, *args)

    def get_loss(self, xs, ys):
        if self.reg == 'dropout':
            _loss_value = self.sess.run(self.loss, feed_dict={self.data_placeholder: xs,
                                                              self.target_placeholder: ys,
                                                              self.keep_prob_placeholder: 1.})
        elif self.reg == 'l2':
            _loss_value = self.sess.run(self.loss, feed_dict={self.data_placeholder: xs,
                                                              self.target_placeholder: ys,
                                                              self.l2_placeholder: 0})
        else:
            _loss_value = self.sess.run(self.loss, feed_dict={self.data_placeholder: xs,
                                                              self.target_placeholder: ys})
        return _loss_value

    def get_W(self, n_layer=None):
        if not(n_layer is None or type(n_layer) is int):
            raise TypeError('layer must to be None or int')
        if n_layer is None:
            _Ws = []
            for layer in self.layers:
                _W = self.sess.run(layer.W)
                _Ws.append(_W)
        else:
            if n_layer >= len(self.layers):
                raise IndexError('Do not have layer %i' % n_layer)
            _Ws = self.sess.run(self.layers[n_layer].W)
        return _Ws

    def get_Wshape(self, n_layer=None):
        if not(n_layer is None or type(n_layer) is int):
            raise TypeError('layer must to be None or int')
        if n_layer is None:
            _Wshape = []
            for layer in self.layers:
                _Wshape.append(layer.W.get_shape())
        else:
            if n_layer >= len(self.layers):
                raise IndexError('Do not have layer %i' % n_layer)
            _Wshape = self.layers[n_layer].W.get_shape()
        return _Wshape

    def get_b(self, n_layer=None):
        if not(n_layer is None or type(n_layer) is int):
            raise TypeError('layer need to be None or int')
        if n_layer is None:
            _bs = []
            for layer in self.layers:
                _b = self.sess.run(layer.b)
                _bs.append(_b)
        else:
            if n_layer >= len(self.layers):
                raise IndexError('Do not have layer %i' % n_layer)
            _bs = self.sess.run(self.layers[n_layer].b)
        return _bs

    def get_bshape(self, n_layer=None):
        if not(n_layer is None or type(n_layer) is int):
            raise TypeError('layer must to be None or int')
        if n_layer is None:
            _bshape = []
            for layer in self.layers:
                _bshape.append(layer.b.get_shape())
        else:
            if n_layer >= len(self.layers):
                raise IndexError('Do not have layer %i' % n_layer)
            _bshape = self.layers[n_layer].b.get_shape()
        return _bshape

    def predict(self, *args, **kwargs):
        raise NotImplementedError("Abstract method")

    def save(self, name='new_model', path=None, global_step=None, replace=False):
        if not hasattr(self, '_saver'):
            self._saver = tfnn.NetworkSaver()
        self._saver.save(self, name, path, global_step, replace=replace)

    def close(self):
        self.sess.close()

    def _add_to_log(self, layer):
        _layer_configs_dict = layer.configs_dict
        _layer_results_dict = layer.results_dict
        self.layers_configs = self.layers_configs.append(_layer_configs_dict, ignore_index=True)
        self.layers_results = self.layers_results.append(_layer_results_dict, ignore_index=True)
        self.layers.append(layer)

    def _init_loss(self):
        """do not use in network.py"""
        self.loss = None
