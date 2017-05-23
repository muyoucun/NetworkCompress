# Dependenct: Utils, Config
import json

import keras
import networkx as nx
import numpy as np
import tensorflow as tf
from keras.backend import tensorflow_backend as ktf
from keras.callbacks import TensorBoard
from keras.layers import Conv2D, Input, Activation
from keras.models import Model
from networkx.readwrite import json_graph

import Config
import Utils
from Config import MyConfig


class Node(object):
    def __init__(self, type, name, config):
        self.type = type
        self.name = name
        self.config = config
        # decrypted:
        # self.input_tensors = []
        # self.output_tensors = []

    def __str__(self):
        return self.name


class CustomTypeEncoder(json.JSONEncoder):
    """A custom JSONEncoder class that knows how to encode core custom
    objects.

    Custom objects are encoded as JSON object literals (ie, dicts) with
    one key, '__TypeName__' where 'TypeName' is the actual name of the
    type to which the object belongs.  That single key maps to another
    object literal which is just the __dict__ of the object encoded."""

    # TYPES = {'Node': Node}
    def default(self, obj):
        if isinstance(obj, Node) or isinstance(obj, keras.layers.Layer):
            key = '__%s__' % obj.__class__.__name__
            return {key: obj.__dict__}
        return json.JSONEncoder.default(self, obj)


def _slice_arrays(arrays, start=None, stop=None):
    """Slice an array or list of arrays.

    This takes an array-like, or a list of
    array-likes, and outputs:
        - arrays[start:stop] if `arrays` is an array-like
        - [x[start:stop] for x in arrays] if `arrays` is a list

    Can also work on list/array of indices: `_slice_arrays(x, indices)`

    # Arguments
        arrays: Single array or list of arrays.
        start: can be an integer index (start index)
            or a list/array of indices
        stop: integer (stop index); should be None if
            `start` was a list.

    # Returns
        A slice of the array(s).
    """
    if isinstance(arrays, list):
        if hasattr(start, '__len__'):
            # hdf5 datasets only support list objects as indices
            if hasattr(start, 'shape'):
                start = start.tolist()
            return [x[start] for x in arrays]
        else:
            return [x[start:stop] for x in arrays]
    else:
        if hasattr(start, '__len__'):
            if hasattr(start, 'shape'):
                start = start.tolist()
            return arrays[start]
        else:
            return arrays[start:stop]


class CustomKerasModel(keras.models.Model):
    def __init__(self):
        super(CustomKerasModel, self).__init__()
        self.cache = {}

    def prepare_fit(self, x=None,
                    y=None,
                    batch_size=32,
                    epochs=1,
                    verbose=1,
                    callbacks=None,
                    validation_split=0.,
                    validation_data=None,
                    shuffle=True,
                    class_weight=None,
                    sample_weight=None,
                    initial_epoch=0,
                    **kwargs):
        from keras.engine.training import *
        # Legacy support
        if 'nb_epoch' in kwargs:
            warnings.warn('The `nb_epoch` argument in `fit` '
                          'has been renamed `epochs`.', stacklevel=2)
            epochs = kwargs.pop('nb_epoch')
        if kwargs:
            raise TypeError('Unrecognized keyword arguments: ' + str(kwargs))

        # Validate user data.
        x, y, sample_weights = self._standardize_user_data(
            x, y,
            sample_weight=sample_weight,
            class_weight=class_weight,
            check_batch_axis=False,
            batch_size=batch_size)
        # Prepare validation data.
        if validation_data:
            do_validation = True
            if len(validation_data) == 2:
                val_x, val_y = validation_data
                val_sample_weight = None
            elif len(validation_data) == 3:
                val_x, val_y, val_sample_weight = validation_data
            else:
                raise ValueError('When passing validation_data, '
                                 'it must contain 2 (x_val, y_val) '
                                 'or 3 (x_val, y_val, val_sample_weights) '
                                 'items, however it contains %d items' %
                                 len(validation_data))

            val_x, val_y, val_sample_weights = self._standardize_user_data(
                val_x, val_y,
                sample_weight=val_sample_weight,
                check_batch_axis=False,
                batch_size=batch_size)
            self._make_test_function()
            val_f = self.test_function
            if self.uses_learning_phase and not isinstance(K.learning_phase(), int):
                val_ins = val_x + val_y + val_sample_weights + [0.]
            else:
                val_ins = val_x + val_y + val_sample_weights

        elif validation_split and 0. < validation_split < 1.:
            do_validation = True
            split_at = int(len(x[0]) * (1. - validation_split))
            x, val_x = (_slice_arrays(x, 0, split_at), _slice_arrays(x, split_at))
            y, val_y = (_slice_arrays(y, 0, split_at), _slice_arrays(y, split_at))
            sample_weights, val_sample_weights = (
                _slice_arrays(sample_weights, 0, split_at),
                _slice_arrays(sample_weights, split_at))
            self._make_test_function()
            val_f = self.test_function
            if self.uses_learning_phase and not isinstance(K.learning_phase(), int):
                val_ins = val_x + val_y + val_sample_weights + [0.]
            else:
                val_ins = val_x + val_y + val_sample_weights
        else:
            do_validation = False
            val_f = None
            val_ins = None

        # Prepare input arrays and training function.
        if self.uses_learning_phase and not isinstance(K.learning_phase(), int):
            ins = x + y + sample_weights + [1.]
        else:
            ins = x + y + sample_weights
        self._make_train_function()
        f = self.train_function

        # Prepare display labels.
        out_labels = self._get_deduped_metrics_names()

        if do_validation:
            callback_metrics = copy.copy(out_labels) + ['val_' + n for n in out_labels]
        else:
            callback_metrics = copy.copy(out_labels)

        return dict(f=f, ins=ins, out_labels=out_labels,
                    batch_size=batch_size, epochs=epochs,
                    verbose=verbose, callbacks=callbacks,
                    val_f=val_f, val_ins=val_ins, shuffle=shuffle,
                    callback_metrics=callback_metrics,
                    initial_epoch=initial_epoch)

    def fit(self, x=None,
            y=None,
            batch_size=32,
            epochs=1,
            verbose=1,
            callbacks=None,
            validation_split=0.,
            validation_data=None,
            shuffle=True,
            class_weight=None,
            sample_weight=None,
            initial_epoch=0,
            **kwargs):
        return self._fit_loop(**self.cache)


class MyGraph(nx.DiGraph):
    def __init__(self, model_l=None):
        super(MyGraph, self).__init__()
        if model_l is not None:
            _nodes = []
            for layer in model_l:
                type = layer[0]
                name = layer[1]
                # name_ind = int(re.findall(r'\d+', name)[0])
                config = layer[2]
                _nodes.append(Node(type, name, config))
                # if type not in self.type2inds.keys():
                #     self.type2inds[type] = [name_ind]
                # else:
                #     self.type2inds[type] += [name_ind]

            self.add_path(_nodes)

    def get_nodes(self, name, next_layer=False, last_layer=False):
        name2node = {node.name: node for node in self.nodes()}
        assert name in name2node.keys(), " Name must be uniqiue"
        node = name2node[name]
        if next_layer:
            return self.successors(node)
        elif last_layer:
            return self.predecessors(node)
        else:
            return [node]

    def update(self):

        self.type2ind = {}
        for node in self.nodes():
            import re
            ind = int(re.findall(r'^\w+?(\d+)$', node.name)[0])
            self.type2ind[node.type] = self.type2ind.get(node.type, []) + [ind]

    def deeper(self, name, new_node):
        node = self.get_nodes(name=name)[0]
        next_node = self.get_nodes(name=name, next_layer=True)[0]
        # TODO maybe more than 1

        # assign new node
        if new_node.name == 'new':
            self.update()
            new_name = new_node.type + \
                       str(
                           1 + max(self.type2ind.get(new_node.type, [0]))
                       )
            new_node.name = new_name

        if new_node.config['filters'] == 'same':
            new_node.config['filters'] = node.config['filters']

        self.remove_edge(node, next_node)
        self.add_edge(node, new_node)
        self.add_edge(new_node, next_node)

    def to_model(self, input_shape, graph, name="default_for_op"):
        with graph.as_default():
            with tf.name_scope(name) as scope:

                graph_helper = self.copy()

                assert nx.is_directed_acyclic_graph(graph_helper)
                topo_nodes = nx.topological_sort(graph_helper)

                input_tensor = Input(shape=input_shape)

                for node in topo_nodes:
                    pre_nodes = graph_helper.predecessors(node)
                    suc_nodes = graph_helper.successors(node)

                    if node.type not in ['Concatenate', 'Add', 'Multiply']:
                        if len(pre_nodes) == 0:
                            layer_input_tensor = input_tensor
                        else:
                            assert len(pre_nodes) == 1
                            layer_input_tensor = graph_helper[pre_nodes[0]][node]['tensor']

                        if node.type == 'Conv2D':
                            kernel_size = node.config.get('kernel_size', 3)
                            filters = node.config['filters']

                            layer = Conv2D(kernel_size=kernel_size, filters=filters, name=node.name, padding='same',
                                           activation='relu')

                        elif node.type == 'GlobalMaxPooling2D':
                            layer = keras.layers.GlobalMaxPooling2D(name=node.name)
                        elif node.type == 'MaxPooling2D':
                            layer = keras.layers.MaxPooling2D(name=node.name)
                        elif node.type == 'AveragePooling2D':
                            layer = keras.layers.AveragePooling2D(name=node.name)
                        elif node.type == 'Activation':
                            activation_type = node.config['activation_type']
                            layer = Activation(activation=activation_type, name=node.name)
                        layer_output_tensor = layer(layer_input_tensor)
                    else:
                        # TODO Add
                        layer_input_tensors = [graph_helper[pre_node][node]['tensor'] for pre_node in pre_nodes]
                        if node.type == 'Concatenate':
                            # handle shape
                            # Either switch to ROIPooling or MaxPooling
                            # TODO consider ROIPooling
                            import keras.backend as K

                            if K.image_data_format() == "channels_last":
                                (width_ind, height_ind, chn_ind) = (1, 2, 3)
                            else:
                                (width_ind, height_ind, chn_ind) = (2, 3, 1)
                            ori_shapes = [
                                ktf.int_shape(layer_input_tensor)[width_ind:height_ind + 1] for layer_input_tensor in
                                layer_input_tensors
                            ]
                            ori_shapes = np.array(ori_shapes)
                            new_shape = ori_shapes.min(axis=0)
                            for ind, layer_input_tensor, ori_shape in \
                                    zip(range(len(layer_input_tensors)), layer_input_tensors, ori_shapes):
                                diff_shape = ori_shape - new_shape
                                if diff_shape.all():
                                    diff_shape += 1
                                    layer_input_tensors[ind] = \
                                        keras.layers.MaxPool2D(pool_size=diff_shape, strides=1)(layer_input_tensor)

                            layer = keras.layers.Concatenate(axis=chn_ind)
                        layer_output_tensor = layer(layer_input_tensors)

                    graph_helper.add_node(node, layer=layer)

                    if len(suc_nodes) == 0:
                        output_tensor = layer_output_tensor
                    else:
                        for suc_node in suc_nodes:
                            graph_helper.add_edge(node, suc_node, tensor=layer_output_tensor)
                assert tf.get_default_graph() == graph, "should be same"
                # tf.train.export_meta_graph('tmp.pbtxt', graph_def=tf.get_default_graph().as_graph_def())
                assert 'output_tensor' in locals()
                import time
                tic = time.time()
                model = Model(inputs=input_tensor, outputs=output_tensor)
                Config.logger.info('Consume Time(Just Build model: {}'.format(time.time() - tic))

        return model

    def to_json(self):
        data = json_graph.node_link_data(self)
        try:
            str = json.dumps(data, indent=2, cls=CustomTypeEncoder)
        except Exception as inst:
            str = ""
            print inst

        return str


class MyModel(object):
    def __init__(self, config, graph):
        self.config = config
        self.graph = graph
        self.model = self.graph.to_model(self.config.input_shape, graph=self.config.tf_graph,
                                         name=self.config.name)

    def get_layers(self, name, next_layer=False, last_layer=False):
        name2layer = {layer.name: layer for layer in self.model.layers}

        def _get_layer(name):
            return name2layer[name]

        nodes = self.graph.get_nodes(name, next_layer, last_layer)
        if not isinstance(nodes, list):
            nodes = [nodes]
        return map(_get_layer, [node.name for node in nodes])

    def compile(self):
        self.model.compile(optimizer='rmsprop',
                           loss='categorical_crossentropy',
                           metrics=['accuracy'])

    def fit(self):
        import time
        tic = time.time()

        self.model.fit(self.config.dataset['train_x'],
                       self.config.dataset['train_y'],
                       # validation_split=0.2,
                       validation_data=(self.config.dataset['test_x'], self.config.dataset['test_y']),
                       batch_size=self.config.batch_size,
                       epochs=self.config.epochs,
                       callbacks=[self.config.lr_reducer, self.config.early_stopper, self.config.csv_logger,
                                  TensorBoard(log_dir=self.config.tf_log_path)]
                       )
        Config.logger.info("Fit model Consume {}:".format(time.time() - tic))

    def evaluate(self):
        score = self.model.evaluate(self.config.dataset['test_x'],
                                    self.config.dataset['test_y'],
                                    batch_size=self.config.batch_size, verbose=self.config.verbose)
        return score

    def comp_fit_eval(self):
        # assert tf.get_default_graph() is self.config.tf_graph, "graph same"
        with self.config.tf_graph.as_default():

            with tf.name_scope(self.config.name) as scope:
                self.compile()

                # self.fit_finished=False
                Utils.vis_model(self.model, self.config.name)
                Utils.vis_graph(self.graph, self.config.name, show=False)  # self.config.dbg
                self.model.summary()
                trainable_count, non_trainable_count = Utils.count_weight(self.model)
                Config.logger.info(
                    "trainable weight {} MB, non trainable_weight {} MB".format(trainable_count, non_trainable_count))

        with self.config.sess.as_default():
            assert tf.get_default_graph() is self.config.tf_graph, "graph same"
            with tf.name_scope(self.config.name) as scope:
                self.fit()

                # score = self.evaluate()
                # print('\n-- loss and accuracy --\n')
                # print(score)


if __name__ == "__main__":

    dbg = True
    if dbg:
        config = MyConfig(epochs=0, verbose=1, dbg=dbg, name='model_test')
    else:
        config = MyConfig(epochs=100, verbose=1, dbg=dbg, name='model_test')
    model_l = [["Conv2D", 'conv1', {'filters': 16}],
               ["Conv2D", 'conv2', {'filters': 64}],
               ["Conv2D", 'conv3', {'filters': 10}],
               ['GlobalMaxPooling2D', 'gmpool1', {}],
               ['Activation', 'activation1', {'activation_type': 'softmax'}]]
    graph = MyGraph(model_l)
    teacher_model = MyModel(config, graph)
    teacher_model.comp_fit_eval()
