from pathlib import Path
import cv2
import torch
from torch import nn
from loguru import logger
from typing import Union
from torchvision import transforms as T
from torchvision.transforms import functional as TF
from kornia import augmentation as K
from functools import partial

import histaug
from .macenko_torchstain import TorchMacenkoNormalizer, FullyTransparentException

PATCH_SIZE = 224

__all__ = ["load_augmentations", "Augmentations"]


class Macenko(nn.Module):
    def __init__(
        self,
        target_image: Path = Path(histaug.__file__).parent.parent / "normalization_template.jpg",
    ):
        super().__init__()
        target = cv2.imread(str(target_image))
        target = cv2.cvtColor(target, cv2.COLOR_BGR2RGB)
        target = torch.from_numpy(target).permute(2, 0, 1)
        self.normalizer = TorchMacenkoNormalizer()
        logger.info(f"Fitting Macenko normalizer to {target_image}")
        self.normalizer.fit(target)

    def forward_image(self, image: torch.Tensor) -> torch.Tensor:
        try:
            Inorm, H, E = self.normalizer.normalize((image * 255).type(torch.uint8))
            return Inorm.type_as(image) / 255
        except FullyTransparentException as e:
            logger.warning(
                f"Attempting to Macenko normalize fully transparent image ({str(e)}). Returning original image."
            )
            return image

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        return torch.stack([self.forward_image(img) for img in images])


def EnlargeAndCenterCrop(zoom_factor: Union[float, int] = 2, patch_size=PATCH_SIZE):
    return T.Compose([T.Resize(int(zoom_factor * patch_size), antialias=True), T.CenterCrop(patch_size)])


class Augmentations(dict):
    def to(self, device):
        for k, v in self.items():
            if isinstance(v, nn.Module):
                self[k] = v.to(device)
        return self


def load_augmentations() -> Augmentations:
    augmentations = Augmentations()
    augmentations["Macenko"] = Macenko()
    augmentations["low brightness"] = T.ColorJitter(brightness=(0.7,) * 2)
    augmentations["high brightness"] = T.ColorJitter(brightness=(1.5,) * 2)
    augmentations["low contrast"] = T.ColorJitter(contrast=(0.7,) * 2)
    augmentations["high contrast"] = T.ColorJitter(contrast=(1.5,) * 2)
    augmentations["low saturation"] = T.ColorJitter(saturation=(0.7,) * 2)
    augmentations["high saturation"] = T.ColorJitter(saturation=(1.5,) * 2)
    augmentations["colour jitter"] = T.ColorJitter(brightness=0.4, contrast=0.4, saturation=0.4, hue=0.1)
    augmentations["gamma 0.5"] = T.Lambda(lambda x: x**0.5)
    augmentations["gamma 2.0"] = T.Lambda(lambda x: x**2.0)

    augmentations["flip horizontal"] = T.RandomHorizontalFlip(p=1.0)
    augmentations["flip vertical"] = T.RandomVerticalFlip(p=1.0)
    augmentations["rotate 90°"] = partial(TF.rotate, angle=90)
    augmentations["rotate 180°"] = partial(TF.rotate, angle=180)
    augmentations["rotate 270°"] = partial(TF.rotate, angle=270)
    augmentations["rotate random angle"] = T.Lambda(
        lambda img: TF.rotate(img, angle=(torch.randint(0, 4, (1,)) * 90 + torch.randint(10, 80, (1,))).item())
    )  # rotate by 0, 90, 180, or 270 degrees plus a random angle between 10 and 80 degrees
    augmentations["zoom 1.5x"] = EnlargeAndCenterCrop(1.5)
    augmentations["zoom 1.75x"] = EnlargeAndCenterCrop(1.75)
    augmentations["zoom 2x"] = EnlargeAndCenterCrop(2)
    augmentations["affine"] = T.RandomAffine(degrees=10, translate=(0.2, 0.2), scale=(0.8, 1.2), shear=10)
    augmentations["warp perspective"] = T.RandomPerspective(p=1.0, distortion_scale=0.2, fill=0)
    augmentations["jigsaw"] = K.RandomJigsaw(p=1.0, grid=(4, 4), same_on_batch=True)
    augmentations["Cutout"] = T.RandomErasing(p=1.0, scale=(0.02, 0.25), ratio=(0.3, 3.3), value=0, inplace=False)
    augmentations["AugMix"] = T.Compose(
        [
            T.Lambda(lambda x: (x * 255).type(torch.uint8)),
            T.AugMix(),
            T.Lambda(lambda x: x.type(torch.float32) / 255),
        ]
    )

    augmentations["sharpen"] = T.RandomAdjustSharpness(p=1.0, sharpness_factor=5.0)
    augmentations["gaussian blur"] = T.GaussianBlur(kernel_size=5, sigma=2.0)
    augmentations["median blur"] = K.RandomMedianBlur(p=1.0, kernel_size=5, same_on_batch=True)
    augmentations["gaussian noise"] = T.Lambda(lambda x: x + torch.randn_like(x) * 0.1)

    return augmentations
