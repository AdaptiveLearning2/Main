import os
import numpy as np
import pandas as pd
import cv2
import matplotlib.pyplot as plt 

# Simple code to read video and ground truth of the UBFC-RPPG DATASET

# If you use the dataset, please cite: 
# S. Bobbia, R. Macwan, Y. Benezeth, A. Mansouri, J. Dubois, 
# Unsupervised skin tissue segmentation for remote photoplethysmography, 
# Pattern Recognition Letters, Elsevier, 2017. 
# yannick.benezeth@u-bourgogne.fr

def process_ubfc_dataset(root_folder='DATASET_2/'):
    """
    This function iterates through subdirectories in the specified root_folder,
    loads ground truth data, normalizes it, and reads video frames.
    """

    print(f"Processing dataset from: {root_folder}")

    # Get list of directories (excluding '.' and '..')
    try:
        all_entries = os.listdir(root_folder)
        dirs = [d for d in all_entries if os.path.isdir(os.path.join(root_folder, d)) and d not in ['.', '..', 'desktop.ini']]
        dirs.sort() # Sort to ensure consistent order if needed
    except FileNotFoundError:
        print(f"Error: Dataset root folder '{root_folder}' not found.")
        return

    if not dirs:
        print(f"No subdirectories found in '{root_folder}'. Please check the dataset structure.")
        return

    # Iterate through all directories
    for i, dir_name in enumerate(dirs):
        vid_folder = os.path.join(root_folder, dir_name)
        print(f"\n--- Processing folder {i+1}/{len(dirs)}: {vid_folder} ---")

        gt_trace = None # PPG signal
        gt_time = None # time steps
        gt_hr = None # Heart rate values provided directly by the sensor.
        # gt_hr values were not used for evaluating our heart rate estimation method.
        # Instead, our evaluation compared heart rate estimations derived from the remote PPG signal 
        # with estimations calculated from the contact PPG signal in gt_trace.
        
        # Load ground truth
        # Try DATASET_1 format first
        gt_filename_dataset1 = os.path.join(vid_folder, 'gtdump.xmp')
        if os.path.exists(gt_filename_dataset1):
            try:
                # MATLAB's csvread is similar to pandas.read_csv
                gt_data = pd.read_csv(gt_filename_dataset1, header=None).values
                gt_trace = gt_data[:, 3] # 4th column (0-indexed is 3)
                gt_time = gt_data[:, 0] / 1000 # 1st column (0-indexed is 0), convert to seconds
                gt_hr = gt_data[:, 1] # 2nd column (0-indexed is 1)
                print(f"\nLoaded ground truth from: {gt_filename_dataset1} (DATASET_1 format)")
            except Exception as e:
                print(f"Error reading {gt_filename_dataset1}: {e}")
        else:
            # Try DATASET_2 format
            gt_filename_dataset2 = os.path.join(vid_folder, 'ground_truth.txt')
            if os.path.exists(gt_filename_dataset2):
                try:
                    # MATLAB's dlmread is similar to numpy.loadtxt or pandas.read_csv with delimiter
                    gt_data = np.loadtxt(gt_filename_dataset2)
                    gt_trace = gt_data[0, :].T # 1st row, transpose
                    gt_time = gt_data[2, :].T # 3rd row, transpose
                    gt_hr = gt_data[1, :].T # 2nd row, transpose
                    print(f"\nLoaded ground truth from: {gt_filename_dataset2} (DATASET_2 format)")
                except Exception as e:
                    print(f"Error reading {gt_filename_dataset2}: {e}")
            else:
                print(f"Warning: No ground truth file found in {vid_folder} (checked gtdump.xmp and ground_truth.txt)")

        print(f"  Number of PPG signal values: {len(gt_trace)}")
        print(f"  Length of ground truth signal: {gt_time[-1]:.2f} seconds")
       
        # Normalize data (zero mean and unit variance)
        gt_trace = gt_trace - np.mean(gt_trace)
        if np.std(gt_trace) != 0:
            gt_trace = gt_trace / np.std(gt_trace)
        else:
            print("Warning: Standard deviation of gt_trace is zero, normalization skipped.")
        
        # Plot after normalization
        if gt_time is not None and len(gt_time) == len(gt_trace):
            plt.figure(figsize=(10, 4))
            plt.plot(gt_time, gt_trace)
            plt.title(f'Normalized Ground Truth Trace for {dir_name}')
            plt.xlabel('Time (seconds)')
            plt.ylabel('Normalized Amplitude')
            plt.tight_layout()
            plt.show()
        else:
            print("Warning: Cannot plot ground truth trace. Time and trace lengths do not match or time data is missing.")

        cv2.destroyAllWindows()


if __name__ == "__main__":
    # Make sure you have the dataset folder in the same directory as this script
    # and it contains the dataset structure (e.g., DATASET_2/subject1/vid.avi, DATASET_2/subject1/ground_truth.txt)
    # process_ubfc_dataset('DATASET_1/')
    process_ubfc_dataset('DATASET_2/')