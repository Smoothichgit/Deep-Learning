from keras import Model
from keras.layers import (Input, Conv2D, ReLU,
                          BatchNormalization, Flatten,
                          Dense, Reshape, Conv2DTranspose,
                          Activation, Lambda)
from keras import backend as K
# from keras.optimizers import Adam
from keras.optimizers.legacy import Adam
from keras.losses import MeanSquaredError
import numpy as np
import os
import pickle
import tensorflow as tf

tf.compat.v1.disable_eager_execution()

def _calculate_reconstruction_loss(y_target, y_predicted):
    error = y_target - y_predicted
    reconstruction_loss = K.mean(K.square(error), axis=[1, 2, 3])
    return reconstruction_loss


def calculate_kl_loss(model):
    # wrap `_calculate_kl_loss` such that it takes the model as an argument,
    # returns a function which can take arbitrary number of arguments
    # (for compatibility with `metrics` and utility in the loss function)
    # and returns the kl loss
    def _calculate_kl_loss(*args):
        kl_loss = -0.5 * K.sum(1 + model.log_variance - K.square(model.mu) -
                               K.exp(model.log_variance), axis=1)
        return kl_loss
    return _calculate_kl_loss

class VAE:
    """
    VAE represents a Deep Convolution variation autoencoder architecture with
    mirrored encoder and decoder components
    """

    def __init__(self,
                 input_shape,
                 conv_filters,
                 conv_kernels,
                 conv_strides,
                 latent_space_dim):
        self.input_shape = input_shape  # [width, height, channel]
        self.conv_filters = conv_filters  # [2, 4, 8] at each conv
        self.conv_kernels = conv_kernels  # [3, 5, 3] at each conv
        self.conv_strides = conv_strides  # [1, 2, 2] at each conv
        self.latent_space_dim = latent_space_dim  # 2 dimension
        self.encoder = None
        self.decoder = None
        self.model = None
        self._model_input = None
        self.reconstruction_loss_weight = 1000

        self._num_conv_layers = len(conv_filters)
        self._shape_before_bottleneck = None
        self._build()

    def summary(self):
        self.encoder.summary()
        self.decoder.summary()
        self.model.summary()

    def compile(self, learning_rate=0.0001):
        optimizer = Adam(learning_rate=learning_rate)
        self.model.compile(optimizer=optimizer,
                           loss=self._calculate_combined_loss,
                           metrics=[_calculate_reconstruction_loss,
                                    calculate_kl_loss(self)])

        # optimizer =Adam(learning_rate=learning_rate)
        # mse_loss = MeanSquaredError()
        # self.model.compile(optimizer=optimizer, loss=mse_loss)

    def train(self, x_train, batch_size, num_epochs):
        self.model.fit(x_train, x_train,
                       batch_size=batch_size,
                       epochs=num_epochs,
                       shuffle=True)

    def load_weights(self, weights_path):
        self.model.load_weights(weights_path)

    def reconstruct(self, images):
        latent_representation = self.encoder.predict(images)
        reconstructed_images = self.decoder.predict(latent_representation)
        return reconstructed_images, latent_representation

    @classmethod
    def load(cls, save_folder="."):
        parameters_path = os.path.join(save_folder, "parameters.pkl")
        with open(parameters_path, "rb") as f:
            parameters = pickle.load(f)
        autoencoder = VAE(*parameters)
        weights_path = os.path.join(save_folder, "weights.h5")
        autoencoder.load_weights(weights_path)
        return autoencoder

    def _calculate_combined_loss(self, y_target, y_predicted):
        reconstruction_loss = _calculate_reconstruction_loss(y_target, y_predicted)
        kl_loss = calculate_kl_loss(self)()
        combined_loss = self.reconstruction_loss_weight * reconstruction_loss + kl_loss
        return combined_loss


    def save(self, save_folder="."):
        self._create_folder_if_it_doesnt_exist(save_folder)
        self._save_parameters(save_folder)
        self._save_weights(save_folder)

    def _create_folder_if_it_doesnt_exist(self, folder):
        if not os.path.exists(folder):
            os.makedirs(folder)

    def _save_parameters(self, save_folder):
        parameters = [
            self.input_shape,  # [width, height, channel]
            self.conv_filters,  # [2, 4, 8] at each conv
            self.conv_kernels,  # [3, 5, 3] at each conv
            self.conv_strides,  # [1, 2, 2] at each conv
            self.latent_space_dim  # 2
        ]
        save_path = os.path.join(save_folder, "parameters.pkl")
        with open(save_path, "wb") as f:
            pickle.dump(parameters, f)

    def _save_weights(self, save_folder):
        save_path = os.path.join(save_folder, "weights.h5")  # .h5 is keras api
        self.model.save_weights(save_path)

    def _build(self):
        self._build_encoder()
        self._build_decoder()
        self._build_autoencoder()

    def _build_autoencoder(self):
        model_input = self._model_input
        model_output = self.decoder(self.encoder(model_input))
        self.model = Model(model_input, model_output, name="autoencoder")

    def _build_decoder(self):
        decoder_input = self._add_decoder_input()
        dense_layer = self._add_dense_layer(decoder_input)
        reshape_layer = self._add_reshape_layer(dense_layer)
        conv_transpose_layer = self._add_conv_transpose_layers(reshape_layer)
        decoder_output = self._add_decoder_output(conv_transpose_layer)
        self.decoder = Model(decoder_input, decoder_output, name="decoder")

    def _add_decoder_input(self):
        return Input(shape=self.latent_space_dim, name="decoder_input")

    def _add_dense_layer(self, decoder_input):
        num_neuron = np.prod(self._shape_before_bottleneck)  # [1,2,4] -> 8
        dense_layer = Dense(num_neuron, name="decoder_dense")(decoder_input)
        return dense_layer

    def _add_reshape_layer(self, dense_layer):
        reshape_layer = Reshape(self._shape_before_bottleneck)(dense_layer)
        return reshape_layer

    def _add_conv_transpose_layers(self, x):
        """ Add conv transpose block"""
        # loop through all the conv layers in reverse order and stop at the
        # first layer
        for layer_index in reversed(range(1, self._num_conv_layers)):
            # [0,1,2] -> [2,1] but ignore first index
            x = self._add_conv_transpose_layer(layer_index, x)
        return x

    def _add_conv_transpose_layer(self, layer_index, x):
        layer_number = self._num_conv_layers - layer_index
        conv_transpose_layer = Conv2DTranspose(
            filters=self.conv_filters[layer_index],
            kernel_size=self.conv_kernels[layer_index],
            strides=self.conv_strides[layer_index],
            padding="same",
            name=f"decoder_cov_transpose_layer_{layer_number}"
        )
        x = conv_transpose_layer(x)
        x = ReLU(name=f"decoder_relu_{layer_number}")(x)
        x = BatchNormalization(name=f"decoder_bn_{layer_number}")(x)
        return x

    def _add_decoder_output(self, x):
        conv_transpose_layer = Conv2DTranspose(
            filters=1,  # [Height, width, channel]
            kernel_size=self.conv_kernels[0],
            strides=self.conv_strides[0],
            padding="same",
            name=f"decoder_cov_transpose_layer_{self._num_conv_layers}"
        )
        x = conv_transpose_layer(x)
        output_layer = Activation("sigmoid", name="sigmoid_layer")(x)
        return x

    def _build_encoder(self):
        encoder_input = self._add_encoder_input()
        conv_layers = self.add_conv_layers(encoder_input)
        bottleneck = self._add_bottleneck(conv_layers)
        self._model_input = encoder_input
        self.encoder = Model(encoder_input, bottleneck, name="encoder")  # (input, output, name)

    def _add_encoder_input(self):
        return Input(shape=self.input_shape, name="encoder_input")

    def add_conv_layers(self, encoder_input):
        # Create all convolutions blocks in encoder.
        x = encoder_input
        for layer_index in range(self._num_conv_layers):
            x = self._add_conv_layer(layer_index, x)
        return x

    def _add_conv_layer(self, layer_index, x):
        """ Adds a convolutional block to a graph of layers
        consisting of conv 2d + Relu + batch normalization
        """
        layer_number = layer_index + 1
        conv_layer = Conv2D(
            filters=self.conv_filters[layer_index],
            kernel_size=self.conv_kernels[layer_index],
            strides=self.conv_strides[layer_index],
            padding="same",
            name=f"encoder_conv_layer{layer_number}"
        )
        x = conv_layer(x)
        x = ReLU(name=f"encoder_relu_{layer_number}")(x)
        x = BatchNormalization(name=f"encoder_bn_{layer_number}")(x)
        return x

    def _add_bottleneck(self, x):
        """Flatten data and add bottleneck with Guassian sampling (Dense
        layer).
        """
        self._shape_before_bottleneck = K.int_shape(x)[1:]
        x = Flatten()(x)
        self.mu = Dense(self.latent_space_dim, name="mu")(x)
        self.log_variance = Dense(self.latent_space_dim,
                                  name="log_variance")(x)

        def sample_point_from_normal_distribution(args):
            mu, log_variance = args
            epsilon = K.random_normal(shape=K.shape(self.mu), mean=0.,
                                      stddev=1.)
            sampled_point = mu + K.exp(log_variance / 2) * epsilon
            return sampled_point

        x = Lambda(sample_point_from_normal_distribution,
                   name="encoder_output")([self.mu, self.log_variance])
        return x


if __name__ == "__main__":
    autoencoder = VAE(
        input_shape=(28, 28, 1),
        conv_filters=(32, 64, 64, 64),
        conv_kernels=(3, 3, 3, 3),
        conv_strides=(1, 2, 2, 1),
        latent_space_dim=2
    )
    autoencoder.summary()
