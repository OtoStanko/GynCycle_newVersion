import random

import numpy as np
import scipy.signal
import tensorflow as tf
from matplotlib import pyplot as plt
from scipy.optimize import curve_fit
from scipy.signal import savgol_filter

from supporting_scripts import sin_function


class ResidualWrapper(tf.keras.Model):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def call(self, inputs, *args, **kwargs):
        delta = self.model(inputs, *args, **kwargs)

        # The prediction for each time step is the input
        # from the previous time step plus the delta
        # calculated by the model.
        return inputs + delta


class MMML(tf.keras.Model):
    def __init__(self, model1, model2, out_steps, num_features):
        super().__init__()
        self.model1 = model1
        self.model2 = model2
        self.out_steps = out_steps
        self.num_features = num_features
        self.mmml = tf.keras.Sequential([
            tf.keras.layers.Dense(256, activation='relu'),
            tf.keras.layers.Dense(64, activation='relu'),
            tf.keras.layers.Dense(num_features, activation='relu'),
        ])

    def call(self, inputs, training=None):
        model1_out = self.model1(inputs, training=training)
        model2_out = self.model2(inputs, training=training)
        if model1_out is None or model2_out is None:
            raise ValueError("One of the model outputs is None.")
        inputs = model1_out + model2_out
        inputs = tf.convert_to_tensor(inputs, dtype=tf.float32)
        result = self.mmml(inputs, training=training)
        result = tf.convert_to_tensor(result, dtype=tf.float32)
        return result


def custom_activation(x, a=1.0):
    return x + (tf.sin(a * x) ** 2)/a


class CNN_LSTM(tf.keras.Model):
    def __init__(self, units, input_length, out_steps, num_features, min_peak_distance=20,
                 filters=None, ks=None, dilations=None):
        super().__init__()
        if dilations is None:
            dilations = [1, 2, 4]
        if ks is None:
            ks = [4, 3, 2]
        if filters is None:
            filters = [32, 64, 128]
        self.units = units
        self.input_length = input_length
        self.out_steps = out_steps
        self.num_features = num_features
        self.num_output_features = num_features
        self.min_peak_distance = min_peak_distance
        self.cnl = tf.keras.Sequential([
            tf.keras.layers.Conv1D(filters=filters[0], kernel_size=ks[0], activation='relu', padding='same',
                                   dilation_rate=dilations[0],
                                   input_shape=(input_length, num_features)),
            tf.keras.layers.Conv1D(filters=filters[1], kernel_size=ks[1], activation='relu', padding='same',
                                   dilation_rate=dilations[1]),
            tf.keras.layers.Conv1D(filters=filters[2], kernel_size=ks[2], activation='relu', padding='same',
                                   dilation_rate=dilations[2]),
            tf.keras.layers.LSTM(32),
            tf.keras.layers.Dense(out_steps * num_features),
            tf.keras.layers.Reshape((out_steps, num_features))
        ])

    def call(self, inputs):
        return self.cnl(inputs)

    def get_peaks(self, prediction, method='raw'):
        """
        For given model predictions identifies peaks in it.

        :param prediction: ndarray of predictions (one feature)
        :param method: NA for this model
        :return: ndarray of indexes where peaks were detected in the input array
        """
        pred_peaks, _ = scipy.signal.find_peaks(prediction, distance=self.min_peak_distance, height=0.2)
        position_of_max = np.argmax(prediction)
        if position_of_max not in pred_peaks:
            index = np.searchsorted(pred_peaks, position_of_max)
            pred_peaks = np.insert(pred_peaks, index, position_of_max)
        return pred_peaks



class FeedBack(tf.keras.Model):
    def __init__(self, units, out_steps, num_features, min_peak_distance=20):
        """
        A feedback model consisting of one RNN layer with LSTM cell and one dense layer.

        :param units: number of units in the LSTM cell
        :param out_steps: output length
        :param num_features: number of input and output features
        :param min_peak_distance: minimum distance for peak detection
        """
        super().__init__()
        self.out_steps = out_steps
        self.units = units
        self.num_features = num_features
        self.num_output_features = num_features
        self.min_peak_distance = min_peak_distance
        self.lstm_cell = tf.keras.layers.LSTMCell(units)
        self.lstm_rnn = tf.keras.layers.RNN(self.lstm_cell, return_state=True)
        self.dense = tf.keras.layers.Dense(num_features)

    def warmup(self, inputs):
        x, *state = self.lstm_rnn(inputs)
        prediction = self.dense(x)
        return prediction, state

    def call(self, inputs, training=None):
        predictions = []
        prediction, state = self.warmup(inputs)
        predictions.append(prediction)

        for n in range(1, self.out_steps):
            x = prediction
            x, state = self.lstm_cell(x, states=state,
                                      training=training)
            prediction = self.dense(x)
            predictions.append(prediction)

        # predictions.shape => (time, batch, features)
        predictions = tf.stack(predictions)
        # predictions.shape => (batch, time, features)
        predictions = tf.transpose(predictions, [1, 0, 2])
        return predictions

    def get_peaks(self, prediction, method='raw'):
        """
        For given model predictions identifies peaks in it.

        :param prediction: ndarray of predictions (one feature)
        :param method: NA for this model
        :return: ndarray of indexes where peaks were detected in the input array
        """
        pred_peaks, _ = scipy.signal.find_peaks(prediction, distance=self.min_peak_distance, height=0.2)
        position_of_max = np.argmax(prediction)
        if position_of_max not in pred_peaks:
            index = np.searchsorted(pred_peaks, position_of_max)
            pred_peaks = np.insert(pred_peaks, index, position_of_max)
        return pred_peaks

    def get_config(self):
        # Return the configuration of the model (needed for saving and loading)
        config = super().get_config().copy()
        config.update({
            "units": self.units,
            "out_steps": self.out_steps,
            "num_features": self.num_features,
            "min_peak_distance": self.min_peak_distance
        })
        return config

    @classmethod
    def from_config(cls, config):
        return cls(**config)


class WideCNN(tf.keras.Model):
    def __init__(self, input_length, out_steps, num_features, min_peak_distance=20):
        """
        Model consisting of one convolutional layer with 256 filers and two dense layers (32, num_features).
        (Model makes one-step prediction and based on the desired output length feeds the prediction with the original
        input back to itself to generate the next output step.)
        Model has been simplified. Feedback loop is no longer present. Instead, a prediction of out_steps is made.
        Results are comparable to the original version and training is shorter.

        :param input_length: length of the input
        :param out_steps: output length
        :param num_features: number of input and output features
        :param min_peak_distance: minimum distance for peak detection
        """
        super().__init__()
        self.input_length = input_length
        self.out_steps = out_steps
        self.num_features = num_features
        self.num_output_features = num_features
        self.min_peak_distance = min_peak_distance
        conv_model_wide = tf.keras.Sequential([
            tf.keras.layers.Conv1D(filters=256,
                                   kernel_size=input_length,
                                   activation='relu',
                                   input_shape=(input_length, num_features),),
            tf.keras.layers.Dense(units=32, activation='relu'),
            tf.keras.layers.Dense(out_steps * num_features),
            tf.keras.layers.Reshape((out_steps, num_features))
        ])
        # lambda x: custom_activation(x, a=20.0)
        self.cnn = conv_model_wide

    def call(self, inputs):
        return self.cnn(inputs)

    def get_peaks(self, prediction, method='raw'):
        """
        For given model predictions identifies peaks in it.

        :param prediction: ndarray of predictions (one feature)
        :param method: NA for this model
        :return: ndarray of indexes where peaks were detected in the input array
        """
        pred_peaks, _ = scipy.signal.find_peaks(prediction, distance=self.min_peak_distance, height=0.2)
        position_of_max = np.argmax(prediction)
        if position_of_max not in pred_peaks:
            index = np.searchsorted(pred_peaks, position_of_max)
            pred_peaks = np.insert(pred_peaks, index, position_of_max)
        return pred_peaks

    def get_config(self):
        # Return the configuration of the model (needed for saving and loading)
        config = super().get_config().copy()
        config.update({
            "input_length": self.input_length,
            "out_steps": self.out_steps,
            "num_features": self.num_features,
            "min_peak_distance": self.min_peak_distance
        })
        return config

    @classmethod
    def from_config(cls, config):
        return cls(**config)


class NoisySinCurve(tf.keras.Model):
    def __init__(self, input_length, out_steps, num_features, train_df, feature,
                 noise=0, shift=0, period=28, min_peak_distance=20):
        """
        Sine curve meant as a baseline for LH prediction. The model cannot be trained. When instantiated a train_df
        with one feature must be provided. This feature will be used to set the function period (mean peak distance).

        Allows adding noise into the output from normal distribution

        :param input_length: length of the input
        :param out_steps: output length
        :param num_features: number of input and output features (should be 1)
        :param train_df: dataset used to determining the period length (in days)
        :param feature: feature from the train_df where peaks should be predicted
        :param noise: scale (std) of normal distribution with mean=0
        :param shift: initial shift along the x-axis
        :param period: initial period (in days)
        :param min_peak_distance: minimum distance for peak detection
        """
        super().__init__()
        self.input_length = input_length
        self.out_steps = out_steps
        self.num_features = num_features
        self.num_output_features = 1
        self.noise = noise / 10
        self.period = period
        self.shift = shift
        self.min_peak_distance = min_peak_distance
        x_data = train_df.index.values
        y_data = train_df[feature].values
        popt, _ = curve_fit(self.move_curve_function, x_data, y_data, p0=[self.shift])
        self.shift = popt
        print(f"Optimal parameters: b={self.shift}, c={self.period}")
        print('0.05 * sin( (x-b) * ((2*pi)/(c*24)) ) + 0.05')
        x_fit = np.linspace(1200, 3500, 100)
        y_fit = sin_function(x_fit, self.shift, self.period)
        plt.plot(train_df.index[:100], train_df[feature][:100], color='black')
        plt.plot(x_fit, y_fit, label='Fitted Curve', color='orange')
        plt.title('Sampled dataframe with raw hours with fitted sin curve')
        plt.xlabel('Time in hours')
        plt.show()

    def call(self, inputs):
        inputs = tf.reshape(inputs, (-1, self.input_length, self.num_features))
        result = tf.py_function(self.numpy_curve_fit, [inputs], tf.float32)
        result = tf.reshape(result, (-1, self.out_steps, self.num_output_features))
        return result

    def numpy_curve_fit(self, inputs):
        """
        As this model does not work on tensors, a call to non-tensor function is done in call method. This method
        takes in tensor with batch_size and for every record in the batch fits and subsequently makes prediction
        that can be used for peak detection.

        :param inputs: tensor of shape (batch_size, input_length, num_features) First feature is kept, others are discarded
        :return: a tensor of shape (batch_size, out_steps, num_output_features(should be 1))
        """
        y_batch_data = tf.squeeze(inputs, axis=-1).numpy()
        x_data = np.arange(self.input_length) * 24
        output_data = []
        for y_data in y_batch_data:
            popt, _ = curve_fit(self.move_curve_function, x_data, y_data, p0=[0])
            x_fit = np.arange(self.input_length, self.input_length + self.out_steps) * 24
            y_fit = sin_function(x_fit, popt[0], self.period)
            noise = np.random.normal(0, self.noise, y_fit.shape)
            y_fit = y_fit + noise
            output_data.append(y_fit)
        stacked_array = np.stack(output_data)
        expanded_array = np.expand_dims(stacked_array, axis=-1)
        tensor = tf.convert_to_tensor(expanded_array)
        return np.array(tensor, dtype=np.float32)

    def move_curve_function(self, x_data, b):
        return sin_function(x_data, b, self.period)

    def get_peaks(self, prediction, method='raw'):
        """
        For given model predictions identifies peaks in it.

        :param prediction: ndarray of predictions (one feature)
        :param method: NA for this model
        :return: ndarray of indexes where peaks were detected in the input array
        """
        pred_peaks, _ = scipy.signal.find_peaks(prediction, distance=self.min_peak_distance)
        return pred_peaks


    def get_config(self):
        # Return the configuration of the model (needed for saving and loading)
        config = super().get_config().copy()
        config.update({
            "input_length": self.input_length,
            "out_steps": self.out_steps,
            "num_features": self.num_features,
            "min_peak_distance": self.min_peak_distance,
            "noise": self.noise,
            "period": self.period,
            "shift": self.shift
        })
        return config

    @classmethod
    def from_config(cls, config):
        return cls(**config)


class ClassificationMLP(tf.keras.Model):
    def __init__(self, input_length, out_steps, num_features, min_peak_distance):
        """
        Classification model with 3 dense layers (256, 64, out_steps). Only outputs one feature where peaks should
        be detected. Can take in multiple features inputs. The output layer has sigmoid activation function. The output
        can thus be interpreted as probability of the peak being at that position.

        :param input_length: length of the input
        :param out_steps: output length
        :param num_features: number of input features - num_output_features = 1
        :param min_peak_distance: minimum distance for peak detection
        """
        super().__init__()
        self.input_length = input_length
        self.out_steps = out_steps
        self.num_features = num_features
        self.num_output_features = 1
        self.min_peak_distance = min_peak_distance
        self.mlp = tf.keras.models.Sequential([
            tf.keras.layers.Reshape((1, num_features * input_length)),
            tf.keras.layers.Dense(units=256, activation='relu'),
            tf.keras.layers.Dense(units=64, activation='relu'),
            tf.keras.layers.Dense(units=out_steps, activation='sigmoid'),
        ])

    def call(self, inputs):
        inputs = tf.reshape(inputs, (-1, self.num_features, self.input_length))
        shape = inputs.shape
        print(shape)
        result = self.mlp(inputs)
        print(result.shape)
        return result

    def get_peaks(self, prediction, method='raw'):
        """
        For given model predictions identifies peaks in it. Allows multiple methods for peak detection in the prediction.


        :param prediction: ndarray of predictions (one feature)
        :param method: 'raw' - treats the prediction as it would be hormone levels. Finds peaks based on the height
        and distance from other peaks. 'smooth' - applies smoothing to the output and then finds peaks as previously
        'combined' - finds peaks as in raw method. Keeps only the first one. In the rest of the prediction finds the highest
        value that can be peak (left and right values are lower) and adds it into the results. Always returns at most two peaks.
        :return: ndarray of indexes where peaks were detected in the input array
        """
        if method == 'raw':
            return self.peaks_raw(prediction, self.min_peak_distance)
        elif method == 'smooth':
            return self.peaks_smoothened(prediction, self.min_peak_distance)
        elif method == 'combined':
            return self.peaks_combined(prediction, self.min_peak_distance)

    def peaks_raw(self, prediction, min_peak_distance):
        pred_peaks, _ = scipy.signal.find_peaks(prediction, distance=min_peak_distance)
        position_of_max = np.argmax(prediction)
        # The following code sometimes results in 2 peaks too close to each other, but that is somehow acceptable
        if position_of_max not in pred_peaks:
            index = np.searchsorted(pred_peaks, position_of_max)
            pred_peaks = np.insert(pred_peaks, index, position_of_max)
        return pred_peaks

    def peaks_smoothened(self, prediction, min_peak_distance):
        result = tf.reshape(prediction, (self.out_steps))
        result = result / 3
        result = savgol_filter(result, 11, 2)
        pred_peaks, _ = scipy.signal.find_peaks(result, distance=min_peak_distance)
        return pred_peaks

    def is_peak(self, index, values):
        if index == 0:  # First element
            return values[index] > values[index + 1]
        elif index == len(values) - 1:  # Last element
            return values[index] > values[index - 1]
        else:  # Middle elements
            return values[index] > values[index - 1] and values[index] > values[index + 1]

    def peaks_combined(self, prediction, min_peak_distance):
        offset = 15
        pred_peaks, _ = scipy.signal.find_peaks(prediction, distance=min_peak_distance)
        first_peak = pred_peaks[0]
        result = np.array([first_peak])
        if first_peak + offset < 35:
            potential_second_peak = prediction[first_peak+offset:]
            sorted_indexes = np.argsort(potential_second_peak)[::-1]
            sorted_indexes += first_peak + offset
            peak_index = None
            for index in sorted_indexes:
                if self.is_peak(index, prediction):
                    peak_index = index
                    break
            if peak_index is not None:
                result = np.append(result, peak_index)
        return result

    def get_config(self):
        # Return the configuration of the model (needed for saving and loading)
        config = super().get_config().copy()
        config.update({
            "input_length": self.input_length,
            "out_steps": self.out_steps,
            "num_features": self.num_features,
            "min_peak_distance": self.min_peak_distance
        })
        return config

    @classmethod
    def from_config(cls, config):
        return cls(**config)


class Distributed_peaks(tf.keras.Model):
    def __init__(self, out_steps, num_features, position):
        super().__init__()
        self.out_steps = out_steps
        self.num_features = num_features
        self.position = position  # Position of the first peak. 0 - out_steps-1

    def call(self, inputs):
        """
        Idea:
            Put one peak based on the position. Try to put another peak roughly 10 (11) days later.
            put som small values as a noise in between
        """
        result = np.array([random.random()/10 for _ in range(self.position-2)])
        result = np.append(result, 0.2)
        result = np.append(result, 0.5)
        result = np.append(result, 0.2)
        result = np.append(result, np.array([random.random()/10 for _ in range(self.out_steps-self.position-1)]))
        result = tf.reshape(result, (1, self.out_steps, self.num_features))
        return result


"""
    Single step models
    input length = _
    output length = 1
"""

class LinearModel(tf.keras.Model):
    def __init__(self, num_features):
        super().__init__()
        self.num_features = num_features
        self.linear = tf.keras.Sequential([
            tf.keras.layers.Dense(units=num_features),
        ])

    def call(self, inputs):
        return self.linear(inputs)

    def interpret(self):
        plt.bar(x=range(self.num_features),
                height=self.linear.layers[0].kernel[:, 0].numpy())
        axis = plt.gca()
        axis.set_xticks(range(self.num_features))
        _ = axis.set_xticklabels(self.num_features, rotation=90)
        plt.show()


class MultiLayerModel(tf.keras.Model):
    """
        # Dense model
        Two hidden layers with relu activation functions
        """
    def __init__(self, num_features):
        super().__init__()
        self.num_features = num_features
        self.dense = tf.keras.Sequential([
            tf.keras.layers.Dense(units=64, activation='relu'),
            tf.keras.layers.Dense(units=64, activation='relu'),
            tf.keras.layers.Dense(units=num_features)
        ])

    def call(self, inputs):
        return self.dense(inputs)


class CNNModel(tf.keras.Model):
    def __init__(self, num_features, input_length):
        super().__init__()
        self.num_features = num_features
        self.conv = tf.keras.Sequential([
            tf.keras.layers.Conv1D(filters=32,
                                   kernel_size=(input_length,),
                                   activation='relu'),
            tf.keras.layers.Dense(units=32, activation='relu'),
            tf.keras.layers.Dense(units=num_features),
        ])

    def call(self, inputs):
        return self.conv(inputs)