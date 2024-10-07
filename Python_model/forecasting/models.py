import numpy as np
import tensorflow as tf
from matplotlib import pyplot as plt
from scipy.optimize import curve_fit

from supporting_scripts import curve_function

class Baseline(tf.keras.Model):
    def __init__(self, label_index=None):
        super().__init__()
        self.label_index = label_index

    def call(self, inputs):
        if self.label_index is None:
            return inputs
        result = inputs[:, :, self.label_index]
        return result[:, :, tf.newaxis]


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


class My_rnn(tf.keras.Model):
    def __init__(self, units, out_steps, num_features):
        super().__init__()
        self.units = units
        self.out_steps = out_steps
        self.num_features = num_features
        self.lstm = tf.keras.layers.LSTM(self.units, return_state=True)
        self.lstm_cell = tf.keras.layers.LSTMCell(units)
        self.rnn_cell = tf.keras.layers.LSTMCell(units)
        self.rnn = tf.keras.layers.RNN(self.rnn_cell, return_state=True)
        self.dense = tf.keras.Sequential([
            tf.keras.layers.Dense(32, activation='relu', kernel_initializer=tf.initializers.he_normal()),
            tf.keras.layers.Dense(num_features),
        ])

    def call(self, inputs, training=None):
        predictions = []
        x, *state_lstm = self.lstm(inputs)
        #x2, *state_rnn = self.rnn(inputs)
        state_rnn = state_lstm
        prediction = self.dense((x))
        predictions.append(prediction)
        # Run the rest of the prediction steps.
        for n in range(1, self.out_steps):
            # Use the last prediction as input.
            x = prediction
            # Execute one lstm step.
            x, state_lstm = self.lstm_cell(x, states=state_lstm,
                                           training=training)
            x, state_rnn = self.rnn_cell(x, states=state_rnn,
                                         training=training)
            # Convert the lstm output to a prediction.
            prediction = self.dense(x)
            # Add the prediction to the output.
            predictions.append(prediction)

        # predictions.shape => (time, batch, features)
        predictions = tf.stack(predictions)
        # predictions.shape => (batch, time, features)
        predictions = tf.transpose(predictions, [1, 0, 2])
        return predictions


class FeedBack(tf.keras.Model):
    def __init__(self, units, out_steps, num_features):
        super().__init__()
        self.out_steps = out_steps
        self.units = units
        self.lstm_cell = tf.keras.layers.LSTMCell(units)
        self.lstm_rnn = tf.keras.layers.RNN(self.lstm_cell, return_state=True)
        self.dense = tf.keras.layers.Dense(num_features, kernel_initializer=tf.initializers.he_normal())

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


class Wide_CNN(tf.keras.Model):
    def __init__(self, input_length, out_steps, num_features):
        super().__init__()
        self.input_length = input_length
        self.out_steps = out_steps
        self.num_features = num_features
        conv_model_wide = tf.keras.Sequential([
            tf.keras.layers.Conv1D(filters=256,
                                   kernel_size=input_length-2,
                                   activation='relu',
                                   input_shape=(input_length, num_features),),
            tf.keras.layers.Dense(units=32, activation='relu'),
            tf.keras.layers.Dense(units=num_features, kernel_initializer=tf.initializers.he_normal()),
        ])
        self.cnn = conv_model_wide

    def call(self, inputs):
        inputs = tf.convert_to_tensor(inputs, dtype=tf.float32)
        input_tensor = inputs
        for i in range(self.out_steps):
            input_data = input_tensor[:, -self.input_length:, :]
            y = self.cnn(input_data)
            input_tensor = tf.concat([input_tensor, y], axis=1)
        predictions = input_tensor[:, -self.out_steps:, :]
        return predictions

class Fit_sinCurve(tf.keras.Model):
    def __init__(self, input_length, out_steps, num_features, train_df, feature):
        super().__init__()
        self.input_length = input_length
        self.out_steps = out_steps
        self.num_features = num_features
        x_data = train_df.index.values
        y_data = train_df[feature].values
        popt, _ = curve_fit(curve_function, x_data, y_data, p0=[1, 1, 25])
        self.a_opt, self.b_opt, self.c_opt = popt
        print(f"Optimal parameters: a={self.a_opt}, b={self.b_opt}, c={self.c_opt}")
        print('a * sin(x * (2*pi/(c*24)) - b)')
        x_fit = np.linspace(1200, 3500, 100)
        y_fit = curve_function(x_fit, *popt)
        plt.plot(train_df.index[:100], train_df[feature][:100], color='black')
        plt.plot(x_fit, y_fit, label='Fitted Curve', color='orange')
        plt.title('Sampled dataframe with raw hours with fitted sin curve')
        plt.ylabel('Time in hours')
        plt.show()

    def call(self, inputs):
        inputs = tf.reshape(inputs, (self.input_length,))
        result = tf.py_function(self.numpy_curve_fit, [inputs], tf.float32)
        result = tf.reshape(result, (1, self.out_steps, self.num_features))
        return result

    def numpy_curve_fit(self, inputs):
        y_data = np.array(inputs)  # Convert TensorFlow tensor to NumPy array
        x_data = np.arange(self.input_length) * 24  # Create x_data array
        popt, _ = curve_fit(self.move_curve_function, x_data, y_data, p0=[self.b_opt])
        x_fit = np.arange(len(inputs), len(inputs) + self.out_steps) * 24
        y_fit = curve_function(x_fit, self.a_opt, popt[0], self.c_opt)
        print(popt)
        return np.array(y_fit, dtype=np.float32)

    def move_curve_function(self, x_data, b):
        return curve_function(x_data, self.a_opt, b, self.c_opt)
