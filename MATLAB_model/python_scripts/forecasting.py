import pandas as pd
import os
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import tensorflow as tf
from statsmodels.tsa.seasonal import seasonal_decompose
from statsmodels.tsa.statespace.sarimax import SARIMAX
from windowGenerator import WindowGenerator
from models import Baseline, ResidualWrapper
import IPython
import IPython.display


def compile_and_fit(model, window, patience=2):
    early_stopping = tf.keras.callbacks.EarlyStopping(monitor='val_loss',
                                                    patience=patience,
                                                    mode='min')
    model.compile(loss=tf.keras.losses.MeanSquaredError(),
                optimizer=tf.keras.optimizers.Adam(),
                metrics=[tf.keras.metrics.MeanAbsoluteError()])
    history = model.fit(window.train, epochs=MAX_EPOCHS,
                      validation_data=window.val,
                      callbacks=[early_stopping])
    return history


def sample_data(original_df, new_index, columns):
    # The records are not evenly distributed. We will do sampling with linear interpolation for the models
    """
        for every time in the new index, find the largest smaller value and smallest larger value
        and interpolate them to get the new value
        Edge case if at least one of the is the same time
    """
    hormone_levels = {key: [] for key in columns}
    i = 0
    for curr_time in new_index:
        while original_df.index[i + 1] < curr_time:
            i += 1
        for feature in columns:
            # index_of_largest_smaller_time = i
            x0 = largest_smaller_time = original_df.index[i]
            y0 = largest_smaller_value = original_df[feature][original_df.index[i]]
            x1 = smallest_larger_time = original_df.index[i + 1]
            y1 = smallest_larger_value = original_df[feature][original_df.index[i + 1]]
            x = curr_time
            if type(curr_time) == np.datetime64:
                diff_x1_x = (x1 - x).total_seconds()
                diff_x_x0 = (x - x0).total_seconds()
                diff_x1_x0 = (x1 - x0).total_seconds()
            else:
                diff_x1_x = (x1 - x)
                diff_x_x0 = (x - x0)
                diff_x1_x0 = (x1 - x0)
            y = y0 * (diff_x1_x / diff_x1_x0) + y1 * (diff_x_x0 / diff_x1_x0)
            hormone_levels[feature].append(y)

    sampled_df = pd.DataFrame()
    for feature in columns:
        sampled_df[feature] = np.array(hormone_levels[feature])
    sampled_df.index = new_index
    sampled_df.index.name = 'DateTime'
    return sampled_df


# Set the parameters
workDir = os.path.join(os.getcwd(), "../outputDir/")
sampling_frequency = 1
sampling_frequency_unit = 'H'
num_initial_days_to_discard = 50
train_test_split_days = 250
test_days_end = 300
hormone = 'FSH'
features = ['FSH', 'E2', 'P4', 'LH']
MAX_EPOCHS = 20


timeFile = os.path.join(workDir, "Time_1.csv")
times = pd.read_csv(timeFile, header=None, names=['Time'])
hormone_levels = [times]
for feature in features:
    feature_file = os.path.join(workDir, "{}_1.csv".format(feature))
    feature_values = pd.read_csv(feature_file, header=None, names=[feature])
    hormone_levels.append(feature_values)

combined_df = pd.concat(hormone_levels, axis=1)
combined_df['Time'] = combined_df['Time'] * 24


# Plot the loaded data
sns.set()
plt.ylabel('{} levels'.format('Hormones'))
plt.xticks(rotation=45)
plt.xlabel('Time in hours')
plt.plot(combined_df['Time'], combined_df[features], )
plt.show()


# Set some starting date that will be needed for index
start_date = pd.Timestamp('2024-01-01')
# We will not use first 50 days of the simulation
data_start_date = start_date + pd.to_timedelta(num_initial_days_to_discard*24, 'h')
# Set the split date for the train and test data
data_tt_split_date = start_date + pd.to_timedelta(train_test_split_days*24, 'h')
# Set the stop date for the test data (end day of the data)
data_stop_date = start_date + pd.to_timedelta(test_days_end * 24, 'h')

# First 50 days of the simulation may be a bit messy and thus we ignore them
filtered_df = combined_df[combined_df['Time'] > num_initial_days_to_discard*24]
filtered_df.set_index('Time', inplace=True)

print(filtered_df.describe().transpose())
filtered_df_timeH = filtered_df.copy()
print(filtered_df[hormone])
print(filtered_df_timeH[hormone])


time_delta = pd.to_timedelta(filtered_df.index, unit='h')
datetime_index = start_date + time_delta
filtered_df.index = datetime_index
filtered_df.index.name = 'DateTime'
print(filtered_df[hormone][0])


# The records are not evenly distributed. We will do sampling with linear interpolation for the models
new_index = pd.date_range(start=data_start_date,
                          end=data_stop_date,
                          freq="{}{}".format(sampling_frequency, sampling_frequency_unit))

sampled_df = sample_data(filtered_df, new_index, features)

plt.plot(sampled_df.index, sampled_df[features], )
plt.show()



sapmled_df_timeH_index = [i for i in range(num_initial_days_to_discard*24, test_days_end*24+1)]
print(sapmled_df_timeH_index)
print(len(sapmled_df_timeH_index))
sampled_df_timeH = sample_data(filtered_df_timeH, sapmled_df_timeH_index, features)

plt.plot(sampled_df_timeH.index, sampled_df_timeH[features], )
plt.show()
"""
days = [23, 24, 25, 26, 27, 28, 29, 30]
for day in days:
    sampled_df_timeH['{} days'.format(day)] = (np.sin((sampled_df_timeH.index * ((2 * np.pi / (day * 24))))) * 5) + 15
    plt.plot(sampled_df_timeH['{} days'.format(day)])
    plt.plot(sampled_df_timeH[hormone])
    plt.title('{} days cycle sin function'.format(day))
    plt.show()
"""
fft = tf.signal.rfft(sampled_df_timeH[hormone])
f_per_dataset = np.arange(0, len(fft))
n_samples_h = len(sampled_df_timeH[hormone])
print(n_samples_h)
hours_per_year = 24*365.2524
years_per_dataset = n_samples_h/(hours_per_year)
print(years_per_dataset)
f_per_year = f_per_dataset/years_per_dataset
plt.step(f_per_year, np.abs(fft))
plt.xscale('log')
plt.ylim(0, 25000)
plt.xlim([0.1, max(plt.xlim())])
plt.xticks([1, 365.2524/12, 365.2524], labels=['1/Year', '1/month', '1/day'])
_ = plt.xlabel('Frequency (log scale)')
plt.show()

column_indices = {name: i for i, name in enumerate(sampled_df_timeH.columns)}

n = len(sampled_df_timeH)
train_df = sampled_df_timeH[0:int(n*0.7)]
val_df = sampled_df_timeH[int(n*0.7):int(n*0.9)]
test_df = sampled_df_timeH[int(n*0.9):]

num_features = sampled_df_timeH.shape[1]
print(num_features)


train_mean = train_df.mean()
train_std = train_df.std()

train_df = (train_df - train_mean) / train_std
val_df = (val_df - train_mean) / train_std
test_df = (test_df - train_mean) / train_std

plt.plot(train_df.index, train_df[hormone], color='black')
plt.plot(val_df.index, val_df[hormone], color='blue')
plt.plot(test_df.index, test_df[hormone], color='red')
plt.show()

w2 = WindowGenerator(input_width=599, label_width=1, shift=1,
                     train_df=train_df, val_df=val_df, test_df=test_df,
                     label_columns=[hormone])
#print(w2)

# Stack three slices, the length of the total window.
example_window = tf.stack([np.array(train_df[:w2.total_window_size]),
                           np.array(train_df[700:700+w2.total_window_size]),
                           np.array(train_df[1400:1400+w2.total_window_size])])

#example_inputs, example_labels = w2.split_window(example_window)
#w2.example = example_inputs, example_labels
#w2.plot()

#print('All shapes are: (batch, time, features)')
#print(f'Window shape: {example_window.shape}')
#print(f'Inputs shape: {example_inputs.shape}')
#print(f'Labels shape: {example_labels.shape}')

print(w2.train.element_spec)
for example_inputs, example_labels in w2.train.take(1):
    print(f'Inputs shape (batch, time, features): {example_inputs.shape}')
    print(f'Labels shape (batch, time, features): {example_labels.shape}')

single_step_window = WindowGenerator(
    input_width=1, label_width=1, shift=1,
    train_df=train_df, val_df=val_df, test_df=test_df,
    label_columns=[hormone])

# Baseline model
baseline = Baseline(label_index=column_indices[hormone])

baseline.compile(loss=tf.keras.losses.MeanSquaredError(),
                 metrics=[tf.keras.metrics.MeanAbsoluteError()])

val_performance = {}
performance = {}
val_performance['Baseline'] = baseline.evaluate(single_step_window.val, return_dict=True)
performance['Baseline'] = baseline.evaluate(single_step_window.test, verbose=0, return_dict=True)


wide_window = WindowGenerator(
    input_width=24, label_width=24, shift=1,
    train_df=train_df, val_df=val_df, test_df=test_df,
    label_columns=[hormone])

#print(wide_window)
wide_window.plot(baseline)

# Linear model
linear = tf.keras.Sequential([
    tf.keras.layers.Dense(units=1)
])
history = compile_and_fit(linear, single_step_window)

val_performance['Linear'] = linear.evaluate(single_step_window.val, return_dict=True)
performance['Linear'] = linear.evaluate(single_step_window.test, verbose=0, return_dict=True)

wide_window.plot(linear)

plt.bar(x = range(len(train_df.columns)),
        height=linear.layers[0].kernel[:,0].numpy())
axis = plt.gca()
axis.set_xticks(range(len(train_df.columns)))
_ = axis.set_xticklabels(train_df.columns, rotation=90)
plt.show()


# Dense model
dense = tf.keras.Sequential([
    tf.keras.layers.Dense(units=64, activation='relu'),
    tf.keras.layers.Dense(units=64, activation='relu'),
    tf.keras.layers.Dense(units=1)
])
history = compile_and_fit(dense, single_step_window)

val_performance['Dense'] = dense.evaluate(single_step_window.val, return_dict=True)
performance['Dense'] = dense.evaluate(single_step_window.test, verbose=0, return_dict=True)

wide_window.plot(dense)


# Multistep dense
# CNN
CONV_WIDTH = 3
conv_window = WindowGenerator(input_width=CONV_WIDTH, label_width=1, shift=1,
                              train_df=train_df, val_df=val_df, test_df=test_df,
                              label_columns=[hormone])

print(conv_window)
conv_model = tf.keras.Sequential([
    tf.keras.layers.Conv1D(filters=32,
                           kernel_size=(CONV_WIDTH,),
                           activation='relu'),
    tf.keras.layers.Dense(units=32, activation='relu'),
    tf.keras.layers.Dense(units=1),
])
history = compile_and_fit(conv_model, conv_window)

IPython.display.clear_output()
val_performance['Conv'] = conv_model.evaluate(conv_window.val, return_dict=True)
performance['Conv'] = conv_model.evaluate(conv_window.test, verbose=0, return_dict=True)

LABEL_WIDTH = 24
INPUT_WIDTH = LABEL_WIDTH + (CONV_WIDTH - 1)
wide_conv_window = WindowGenerator( input_width=INPUT_WIDTH, label_width=LABEL_WIDTH, shift=1,
                                    train_df=train_df, val_df=val_df, test_df=test_df,
                                    label_columns=[hormone])

wide_conv_window.plot(conv_model)



# RNN model
# LSTM long short-term memory
lstm_model = tf.keras.models.Sequential([
    # Shape [batch, time, features] => [batch, time, lstm_units]
    tf.keras.layers.LSTM(32, return_sequences=True),
    # Shape => [batch, time, features]
    tf.keras.layers.Dense(units=1)
])
history = compile_and_fit(lstm_model, wide_window)

IPython.display.clear_output()
val_performance['LSTM'] = lstm_model.evaluate(wide_window.val, return_dict=True)
performance['LSTM'] = lstm_model.evaluate(wide_window.test, verbose=0, return_dict=True)

wide_window.plot(lstm_model)


# Residual connections
residual_lstm = ResidualWrapper(
    tf.keras.Sequential([
    tf.keras.layers.LSTM(32, return_sequences=True),
    tf.keras.layers.Dense(
        num_features,
        # The predicted deltas should start small.
        # Therefore, initialize the output layer with zeros.
        kernel_initializer=tf.initializers.zeros())
]))

history = compile_and_fit(residual_lstm, wide_window)

IPython.display.clear_output()
val_performance['Residual LSTM'] = residual_lstm.evaluate(wide_window.val, return_dict=True)
performance['Residual LSTM'] = residual_lstm.evaluate(wide_window.test, verbose=0, return_dict=True)



# Performance
x = np.arange(len(performance))
width = 0.3
metric_name = 'mean_absolute_error'
val_mae = [v[metric_name] for v in val_performance.values()]
test_mae = [v[metric_name] for v in performance.values()]

plt.ylabel('mean_absolute_error [T (degC), normalized]')
plt.bar(x - 0.17, val_mae, width, label='Validation')
plt.bar(x + 0.17, test_mae, width, label='Test')
plt.xticks(ticks=x, labels=performance.keys(),
           rotation=45)
_ = plt.legend()
plt.show()

for name, value in performance.items():
    print(f'{name:12s}: {value[metric_name]:0.4f}')



"""
sampled_train_df = sampled_df[(sampled_df.index > data_start_date) & (sampled_df.index <= data_tt_split_date)]
sampled_test_df = sampled_df[(sampled_df.index > data_tt_split_date) & (sampled_df.index <= data_stop_date)]

plt.plot(sampled_train_df.index, sampled_train_df[hormone], color = "black")
plt.plot(sampled_test_df.index, sampled_test_df[hormone], color = "red")
plt.show()
"""

"""
# Fit the model
order = (1,1,0)
seasonal_order = (1,1,0,36)
mod = SARIMAX(sampled_train_df[hormone], trend='c', order=order, seasonal_order=seasonal_order,
              freq="{}{}".format(sampling_frequency, sampling_frequency_unit))
res = mod.fit(disp=False)
print(res.summary())
"""
