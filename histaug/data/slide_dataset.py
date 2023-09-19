from torch.utils.data import Dataset
from pathlib import Path
from typing import Union, Optional, Sequence
import zarr
import torch
import math


class SlideDataset(Dataset):
    def __init__(
        self,
        slide: Union[str, Path],
        augmentations: Optional[Sequence[str]] = None,
        batch_size: Optional[int] = None,
    ):
        slide = Path(slide)
        self.zarr_group = zarr.open_group(str(slide), mode="r")
        self.augmentations = augmentations
        self.batch_size = batch_size
        self.num_patches = self.zarr_group["patches"].shape[0]
        self.num_batches = math.ceil(self.num_patches / self.batch_size) if self.batch_size else 1
        self.name = slide.stem

    def __getitem__(self, index):
        start = index * self.batch_size
        end = min(start + self.batch_size, self.num_patches)
        patches = self.zarr_group["patches"][start:end]
        coords = self.zarr_group["coords"][start:end]
        return self.transform(patches), coords

    def __len__(self):
        return self.num_batches

    @property
    def coords(self):
        return self.zarr_group["coords"][:]

    def transform(self, patches):
        return (torch.from_numpy(patches).float() / 255).permute(0, 3, 1, 2)

    def inverse_transform(self, patches):
        return (patches * 255).uint8().permute(0, 2, 3, 1).numpy()


class SlidesDataset:
    def __init__(self, root: Union[str, Path], batch_size: Optional[int] = None):
        """This dataset is a collection of patches from the slides in the root directory.
        Each element of the dataset is a batch of patches from a single slide.

        Args:
            root (Union[str, Path]): Path to the root directory of the dataset.
            batch_size (Optional[int], optional): Number of patches per iteration. Defaults to None (all patches).
        """
        super().__init__()

        self.root = Path(root)
        self.slides = list(self.root.glob("*.zarr"))
        self.batch_size = batch_size

    def __getitem__(self, index) -> SlideDataset:
        return SlideDataset(self.slides[index], batch_size=self.batch_size)

    def __len__(self):
        return len(self.slides)

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]

    transform = SlideDataset.transform
    inverse_transform = SlideDataset.inverse_transform
