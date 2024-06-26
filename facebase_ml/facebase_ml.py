from typing import List, Callable
import xml.etree.ElementTree as ET
import pandas as pd
from pathlib import Path
from PIL import Image
from deriva_ml.deriva_ml_base import DerivaML, DerivaMLException
from pathlib import Path, PurePath
import os
import sys

import tensorflow as tf
import numpy as np
import nibabel as nib
from scipy.ndimage import zoom, rotate
from tensorflow.image import adjust_brightness
from tensorflow.keras import Sequential, Input

from tensorflow.keras.callbacks import ModelCheckpoint
from tensorflow.keras.optimizers import Adam


# import re


class FaceBaseMLException(DerivaMLException):
    def __init__(self, msg=""):
        super().__init__(msg=msg)


class FaceBaseML(DerivaML):
    """
    FaceBaseML is a class that extends DerivaML and provides additional routines for working with Facebase
    catalogs using deriva-py.

    Attributes:
    - protocol (str): The protocol used to connect to the catalog (e.g., "https").
    - hostname (str): The hostname of the server where the catalog is located.
    - catalog_number (str): The catalog number or name.
    - credential (object): The credential object used for authentication.
    - catalog (ErmrestCatalog): The ErmrestCatalog object representing the catalog.
    - pb (PathBuilder): The PathBuilder object for constructing URL paths.

    Methods:
    - __init__(self, hostname: str = 'ml.facebase.org', catalog_number: str = 'eye-ai'): Initializes the EyeAI object.
    """

    def __init__(self, hostname: str = 'ml.facebase.org', catalog_id: str = 'fb-ml',
                 cache_dir: str = '/data', working_dir: str = None):
        """
        Initializes the FacebaseML object.

        Args:
        - hostname (str): The hostname of the server where the catalog is located.
        - catalog_number (str): The catalog number or name.
        """

        super().__init__(hostname, catalog_id, 'ml', 
                         cache_dir, 
                         working_dir,
                         sys.modules[globals()["__package__"]].__version__)

    def join_and_save_csv(self, base_dir, biosample_filename, genotype_filename, output_filename):
        biosample_path = os.path.join(base_dir, biosample_filename)
        genotype_path = os.path.join(base_dir, genotype_filename)
        output_path = os.path.join(base_dir, output_filename)

        biosample_df = pd.read_csv(biosample_path)
        genotype_df = pd.read_csv(genotype_path)

        merged_df = pd.merge(biosample_df, genotype_df, left_on='genotype', right_on='id')
        final_df = merged_df[['local_identifier', 'name']]
        final_df = final_df.rename(columns={'local_identifier': 'Biosample', 'name': 'genotype'})

        # Directly convert genotype to binary labels here
        final_df['label'] = final_df['genotype'].apply(lambda x: 0 if x.endswith('+/+') else 1)
        final_df.drop(columns=['genotype'], inplace=True)

        final_df.to_csv(output_path, index=False)
        return final_df, output_path

    def load_images_and_labels(self, csv_path, images_folder_path):
        data = pd.read_csv(csv_path)
        data['image_path'] = data['Biosample'].apply(lambda x: os.path.join(images_folder_path, f"{x}.mnc"))
        data = data[data['image_path'].apply(os.path.exists)]

        if data.empty:
            print("No image files found.")
            return [], []

        return data['image_path'].tolist(), data['label'].tolist()

    def preprocess_and_augment_image(self, file_path, label, augment_type):
        image = tf.py_function(func=self.load_process_and_augment_image, inp=[file_path, augment_type], Tout=tf.float32)
        image.set_shape((128, 128, 128, 1))
        return image, label

    def load_process_and_augment_image(self, file_path, augment_type):
        try:
            image = nib.load(file_path.numpy().decode())
            image_data = image.get_fdata()
            processed_image = self.downsample_and_normalize_image(image_data)

            if augment_type.numpy().decode() == 'rotate':
                processed_image = self.augment_image_rotate(processed_image)
            elif augment_type.numpy().decode() == 'brightness':
                processed_image = self.augment_image_brightness(processed_image)

            return processed_image
        except Exception as e:
            print(f"Failed to process file {file_path.numpy().decode()}: {str(e)}")
            return np.zeros((128, 128, 128, 1), dtype=np.float32)

    def downsample_and_normalize_image(self, image_data, target_shape=(128, 128, 128)):
        scale_factors = (np.array(target_shape) / np.array(image_data.shape))
        resized_image = zoom(image_data, scale_factors, order=1)
        normalized_image = (resized_image - np.min(resized_image)) / (np.max(resized_image) - np.min(resized_image))
        normalized_image = normalized_image[..., np.newaxis]
        return normalized_image.astype(np.float32)

    def augment_image_rotate(self, image_data, angle=10):
        return rotate(image_data, angle, axes=(0, 1), reshape=False, mode='nearest')

    def augment_image_brightness(self, image_data, delta=0.1):
        image_tensor = tf.convert_to_tensor(image_data, dtype=tf.float32)
        brightened_image = adjust_brightness(image_tensor, delta)
        return brightened_image.numpy()

    def prepare_dataset(self, image_paths, labels, batch_size, shuffle=False, augment_type=None):
        augment_type = tf.constant(augment_type if augment_type else '')
        dataset = tf.data.Dataset.from_tensor_slices((image_paths, labels))
        dataset = dataset.map(lambda x, y: self.preprocess_and_augment_image(x, y, augment_type), num_parallel_calls=tf.data.AUTOTUNE)
        if shuffle:
            dataset = dataset.shuffle(buffer_size=10)
        dataset = dataset.batch(batch_size)
        return dataset