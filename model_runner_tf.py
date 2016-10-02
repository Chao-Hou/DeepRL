import numpy as np
import os
import random
import math
import time
import threading
import traceback
import pickle
import tensorflow as tf
from rmsprop_applier import RMSPropApplier

def new_session(graph=None):
    config = tf.ConfigProto()
    #config.gpu_options.allow_growth = True
    config.gpu_options.per_process_gpu_memory_fraction = 0.2
    return tf.Session(config=config, graph=graph)
    
def build_network(name, network_type, trainable, num_actions):
    if network_type == 'nips':
        return build_network_nips(name, trainable, num_actions)
    else:
        return build_network_nature(name, trainable, num_actions)
    
def build_network_nature(name, trainable, num_actions):
    
    print("Building network for %s trainable=%s" % (name, trainable))

    with tf.variable_scope(name):
        # First layer takes a screen, and shrinks by 2x
        x = tf.placeholder(tf.uint8, shape=[None, 84, 84, 4], name="screens")
        print(x)
    
        x_normalized = tf.to_float(x) / 255.0
        print(x_normalized)

        # Second layer convolves 32 8x8 filters with stride 4 with relu
        W_conv1, b_conv1 = make_layer_variables([8, 8, 4, 32], trainable, "conv1")

        h_conv1 = tf.nn.relu(tf.nn.conv2d(x_normalized, W_conv1, strides=[1, 4, 4, 1], padding='VALID') + b_conv1, name="h_conv1")
        print(h_conv1)

        # Third layer convolves 64 4x4 filters with stride 2 with relu
        W_conv2, b_conv2 = make_layer_variables([4, 4, 32, 64], trainable, "conv2")

        h_conv2 = tf.nn.relu(tf.nn.conv2d(h_conv1, W_conv2, strides=[1, 2, 2, 1], padding='VALID') + b_conv2, name="h_conv2")
        print(h_conv2)

        # Fourth layer convolves 64 3x3 filters with stride 1 with relu
        W_conv3, b_conv3 = make_layer_variables([3, 3, 64, 64], trainable, "conv3")

        h_conv3 = tf.nn.relu(tf.nn.conv2d(h_conv2, W_conv3, strides=[1, 1, 1, 1], padding='VALID') + b_conv3, name="h_conv3")
        print(h_conv3)

        h_conv3_flat = tf.reshape(h_conv3, [-1, 7 * 7 * 64], name="h_conv3_flat")
        print(h_conv3_flat)

        # Fifth layer is fully connected with 512 relu units
        W_fc1, b_fc1 = make_layer_variables([7 * 7 * 64, 512], trainable, "fc1")

        h_fc1 = tf.nn.relu(tf.matmul(h_conv3_flat, W_fc1) + b_fc1, name="h_fc1")
        print(h_fc1)

        W_fc2, b_fc2 = make_layer_variables([512, num_actions], trainable, "fc2")

        y = tf.matmul(h_fc1, W_fc2) + b_fc2
        print(y)
    
    variables = [W_conv1, b_conv1, W_conv2, b_conv2, W_conv3, b_conv3, W_fc1, b_fc1, W_fc2, b_fc2]
    
    return x, y, variables
    
def build_network_nips(name, trainable, num_actions):
    
    print("Building network for %s trainable=%s" % (name, trainable))

    with tf.variable_scope(name):
        # First layer takes a screen, and shrinks by 2x
        x = tf.placeholder(tf.uint8, shape=[None, 84, 84, 4], name="screens")
        print(x)
    
        x_normalized = tf.to_float(x) / 255.0
        print(x_normalized)

        # Second layer convolves 16 8x8 filters with stride 4 with relu
        W_conv1, b_conv1 = make_layer_variables([8, 8, 4, 16], trainable, "conv1")

        h_conv1 = tf.nn.relu(tf.nn.conv2d(x_normalized, W_conv1, strides=[1, 4, 4, 1], padding='VALID') + b_conv1, name="h_conv1")
        print(h_conv1)

        # Third layer convolves 32 4x4 filters with stride 2 with relu
        W_conv2, b_conv2 = make_layer_variables([4, 4, 16, 32], trainable, "conv2")

        h_conv2 = tf.nn.relu(tf.nn.conv2d(h_conv1, W_conv2, strides=[1, 2, 2, 1], padding='VALID') + b_conv2, name="h_conv2")
        print(h_conv2)

        h_conv2_flat = tf.reshape(h_conv2, [-1, 9 * 9 * 32], name="h_conv2_flat")
        print(h_conv2_flat)

        # Fourth layer is fully connected with 256 relu units
        W_fc1, b_fc1 = make_layer_variables([9 * 9 * 32, 256], trainable, "fc1")

        h_fc1 = tf.nn.relu(tf.matmul(h_conv2_flat, W_fc1) + b_fc1, name="h_fc1")
        print(h_fc1)

        W_fc2, b_fc2 = make_layer_variables([256, num_actions], trainable, "fc2")

        y = tf.matmul(h_fc1, W_fc2) + b_fc2
        print(y)
    
    variables = [W_conv1, b_conv1, W_conv2, b_conv2, W_fc1, b_fc1, W_fc2, b_fc2]
    
    return x, y, variables

def make_layer_variables(shape, trainable, name_suffix):
    stdv = 1.0 / math.sqrt(np.prod(shape[0:-1]))
    weights = tf.Variable(tf.random_uniform(shape, minval=-stdv, maxval=stdv), trainable=trainable, name='W_' + name_suffix)
    biases  = tf.Variable(tf.random_uniform([shape[-1]], minval=-stdv, maxval=stdv), trainable=trainable, name='b_' + name_suffix)
    return weights, biases

class ModelRunnerTF(object):
    def __init__(self, settings,  max_action_no, batch_dimension):
        learning_rate = settings['learning_rate']
        rms_decay = settings['rms_decay']
        rms_epsilon =  settings['rms_epsilon']
        network_type = settings['network_type']
        
        self.step_no = 0
        self.settings = settings
        self.train_batch_size = settings['train_batch_size']
        self.discount_factor = settings['discount_factor']
        self.max_action_no = max_action_no
        self.be = None
        self.history_buffer = np.zeros((1, batch_dimension[1], batch_dimension[2], batch_dimension[3]), dtype=np.float32)
        self.action_mat = np.zeros((self.train_batch_size, self.max_action_no))

        self.init_models(network_type, max_action_no, learning_rate, rms_decay, rms_epsilon)
        
        print("Network Initialized")

    def init_models(self, network_type, max_action_no, learning_rate, rms_decay, rms_epsilon):
        self.sess = new_session()

        self.x, self.y, self.var_train = build_network('policy', network_type, True, max_action_no)
        self.x_target, self.y_target, self.var_target = build_network('target', network_type, False, max_action_no)

        # build the variable copy ops
        self.update_target = []
        for i in range(0, len(self.var_target)):
            self.update_target.append(self.var_target[i].assign(self.var_train[i]))

        self.a = tf.placeholder(tf.float32, shape=[None, max_action_no])
        print('a %s' % (self.a.get_shape()))
        self.y_ = tf.placeholder(tf.float32, [None])
        print('y_ %s' % (self.y_.get_shape()))

        self.y_a = tf.reduce_sum(tf.mul(self.y, self.a), reduction_indices=1)
        print('y_a %s' % (self.y_a.get_shape()))

        optimizer = tf.train.RMSPropOptimizer(learning_rate, decay=rms_decay, epsilon=rms_epsilon)
        self.difference = tf.abs(self.y_a - self.y_)
        quadratic_part = tf.clip_by_value(self.difference, 0.0, 1.0)
        linear_part = self.difference - quadratic_part
        #self.errors = (0.5 * tf.square(quadratic_part)) + linear_part
        self.errors = 0.5 * tf.square(self.difference)
        if self.settings['prioritized_replay'] == True:
            self.priority_weight = tf.placeholder(tf.float32, shape=self.errors.get_shape(), name="priority_weight")
            self.errors2 = tf.mul(self.errors, self.priority_weight)
        else:
            self.errors2 = self.errors
        if self.settings['clip_delta'] == True:  
            self.loss = tf.reduce_sum(tf.clip_by_value(self.errors2, 0.0, 1.0))
        else:
            self.loss = tf.reduce_sum(self.errors2)

        self.train_step = optimizer.minimize(self.loss)
        
        self.saver = tf.train.Saver(max_to_keep=25)

        # Initialize variables
        self.sess.run(tf.initialize_all_variables())
        self.sess.run(self.update_target) # is this necessary?

    def add_to_history_buffer(self, state):
        self.history_buffer[0, :, :, :-1] = self.history_buffer[0, :, :, 1:]
        self.history_buffer[0, :, :, -1] = state

    def clear_history_buffer(self):
        self.history_buffer.fill(0)

    def clip_reward(self, reward):
            if reward > 0:
                return 1
            elif reward < 0:
                return -1
            else:
                return 0

    def predict(self, history_buffer):
        return self.sess.run([self.y], {self.x: history_buffer})[0]
        
    def train(self, minibatch, replay_memory, debug):
        global global_step_no

        if self.settings['prioritized_replay'] == True:
            prestates, actions, rewards, poststates, terminals, replay_indexes, heap_indexes, weights = minibatch
        else:
            prestates, actions, rewards, poststates, terminals = minibatch
        
        self.step_no += 1
        
        y2 = self.y_target.eval(feed_dict={self.x_target: poststates}, session=self.sess)
        
        if self.settings['double_dqn'] == True:
            y3 = self.y.eval(feed_dict={self.x: poststates}, session=self.sess)

        self.action_mat.fill(0)
        y_ = np.zeros(self.train_batch_size)
        
        for i in range(self.train_batch_size):
            self.action_mat[i, actions[i]] = 1
            clipped_reward = self.clip_reward(rewards[i])
            if terminals[i]:
                y_[i] = clipped_reward
            else:
                if self.settings['double_dqn'] == True:
                    max_index = np.argmax(y3[i])
                    y_[i] = clipped_reward + self.discount_factor * y2[i][max_index]
                else:
                    y_[i] = clipped_reward + self.discount_factor * np.max(y2[i])

        if self.settings['prioritized_replay'] == True:
            delta_value, _, y_a = self.sess.run([self.difference, self.train_step, self.y_a], feed_dict={
                self.x: prestates,
                self.a: self.action_mat,
                self.y_: y_,
                self.priority_weight: weights
            })
            for i in range(self.train_batch_size):
                replay_memory.update_td(heap_indexes[i], abs(delta_value[i]))
                if debug:
                    print 'y_- y_a[%s]: %.5f, y_: %.5f, y_a: %.5f' % (i, (y_[i] - y_a[i]), y_[i], y_a[i]) 
                    print 'weight[%s]: %.5f, delta: %.5f, newDelta: %.5f' % (i, weights[i], delta_value[i], weights[i] * delta_value[i]) 
        else:
            self.sess.run(self.train_step, feed_dict={
                self.x: prestates,
                self.a: self.action_mat,
                self.y_: y_
            })

    def update_model(self):
        self.sess.run(self.update_target)

    def load(self, fileName):
        self.saver.restore(self.sess, fileName)
        self.update_model()
        
    def save(self, fileName):
        self.saver.save(self.sess, fileName)
        
