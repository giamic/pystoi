import numpy as np
import warnings
from . import utils

# Constant definition
FS = 10000                          # Sampling frequency
N_FRAME = 256                       # Window support
NFFT = 512                          # FFT Size
NUMBAND = 15                        # Number of 13 octave band
MINFREQ = 150                       # Center frequency of 1st octave band (Hz)
OBM, CF = utils.thirdoct(FS, NFFT, NUMBAND, MINFREQ)  # Get 1/3 octave band matrix
N = 30                              # N. frames for intermediate intelligibility
BETA = -15.                         # Lower SDR bound
DYN_RANGE = 40                      # Speech dynamic range


def stoi(x, y, fs_sig, extended=False):
    """ Short term objective intelligibility
    Computes the STOI (See [1][2]) of a denoised signal compared to a clean
    signal, The output is expected to have a monotonic relation with the
    subjective speech-intelligibility, where a higher score denotes better
    speech intelligibility.

    # Arguments
        x (np.ndarray): clean original speech
        y (np.ndarray): denoised speech
        fs_sig (int): sampling rate of x and y
        extended (bool): Boolean, whether to use the extended STOI described in [3]

    # Returns
        float: Short time objective intelligibility measure between clean and
        denoised speech

    # Raises
        AssertionError : if x and y have different lengths

    # Reference
        [1] C.H.Taal, R.C.Hendriks, R.Heusdens, J.Jensen 'A Short-Time
            Objective Intelligibility Measure for Time-Frequency Weighted Noisy
            Speech', ICASSP 2010, Texas, Dallas.
        [2] C.H.Taal, R.C.Hendriks, R.Heusdens, J.Jensen 'An Algorithm for
            Intelligibility Prediction of Time-Frequency Weighted Noisy Speech',
            IEEE Transactions on Audio, Speech, and Language Processing, 2011.
        [3] Jesper Jensen and Cees H. Taal, 'An Algorithm for Predicting the
            Intelligibility of Speech Masked by Modulated Noise Maskers',
            IEEE Transactions on Audio, Speech and Language Processing, 2016.
    """
    if x.shape != y.shape:
        raise Exception('x and y should have the same shape,' +
                        'found {} and {}'.format(x.shape, y.shape))

    if len(x.shape) == 1:  # Add a batch size if missing
        x = x[None, :]
        y = y[None, :]

    # Resample is fs_sig is different than fs
    if fs_sig != FS:
        x = utils.resample_oct(x, FS, fs_sig)
        y = utils.resample_oct(y, FS, fs_sig)

    # Remove silent frames
    x, y = utils.remove_silent_frames(x, y, DYN_RANGE, N_FRAME, int(N_FRAME/2))

    # Take STFT
    x_spec = utils.stft(x, N_FRAME, NFFT, overlap=2)
    y_spec = utils.stft(y, N_FRAME, NFFT, overlap=2)

    # Ensure at least 30 frames for intermediate intelligibility
    mask = ~np.all(x_spec == 0, axis=-1)
    if np.any(np.sum(mask, axis=-1) < N):
        warnings.warn('Not enough STFT frames to compute intermediate '
                      'intelligibility measure after removing silent '
                      'frames. Returning 1e-5. Please check you wav files',
                      RuntimeWarning)
        return np.squeeze([1e-5 for _ in range(x)])

    # Apply OB matrix to the spectrograms as in Eq. (1)
    x_tob = np.sqrt(np.matmul(np.square(np.abs(x_spec)), OBM.T))
    y_tob = np.sqrt(np.matmul(np.square(np.abs(y_spec)), OBM.T))

    # Take segments of x_tob, y_tob, shape (batch, num_segments, seg_size, bands)
    x_segments = np.array(
        [x_tob[:, m - N : m] for m in range(N, x_tob.shape[1] + 1)]
    ).transpose([1, 0, 2, 3])
    x_segments = x_segments * mask[:, N-1:, None, None]
    y_segments = np.array(
        [y_tob[:, m - N : m] for m in range(N, x_tob.shape[1] + 1)]
    ).transpose([1, 0, 2, 3])
    y_segments = y_segments * mask[:, N-1:, None, None]

    if extended:  # TODO: Vectorialise this
        x_n = np.array([utils.row_col_normalize(xi) for xi in x_segments])
        y_n = np.array([utils.row_col_normalize(yi) for yi in y_segments])
        return np.squeeze(np.sum(x_n * y_n / N, axis=(1, 2, 3)) / x_n.shape[1])

    else:
        # Find normalization constants and normalize
        normalization_consts = np.linalg.norm(x_segments, axis=-1, keepdims=True) / (
            np.linalg.norm(y_segments, axis=-1, keepdims=True) + utils.EPS
        )
        y_segments_normalized = y_segments * normalization_consts

        # Clip as described in [1]
        clip_value = 10 ** (-BETA / 20)
        y_primes = np.minimum(y_segments_normalized, x_segments * (1 + clip_value))

        # Subtract mean vectors
        y_primes = y_primes - np.mean(y_primes, axis=-1, keepdims=True)
        x_segments = x_segments - np.mean(x_segments, axis=-1, keepdims=True)

        # Divide by their norms
        y_primes /= np.linalg.norm(y_primes, axis=-1, keepdims=True) + utils.EPS
        x_segments /= np.linalg.norm(x_segments, axis=-1, keepdims=True) + utils.EPS
        # Find a matrix with entries summing to sum of correlations of vectors
        correlations_components = np.sum(y_primes * x_segments, axis=-2)

        # J, M as in [1], eq.6
        J = x_segments.shape[2]
        M = x_segments.shape[1]

        # Find the mean of all correlations
        d = np.sum(correlations_components, axis=(-2, -1)) / (J * M)
        return np.squeeze(d)
