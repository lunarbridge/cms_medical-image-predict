import numpy as np
import ray
from skimage.transform import resize


@ray.remote
class _SlideActor:
    def __init__(self, slide_path, level=0):
        import openslide

        self.slide = openslide.open_slide(slide_path)
        self.level = level

    def crop(self, crop_coordinate, crop_size, tile_size, crop_scale):
        y, x = crop_coordinate
        x = int(x)
        y = int(y)
        slide_tile = self.slide.read_region((x, y), self.level, (crop_size, crop_size))
        slide_tile = np.asarray(slide_tile)[:, :, 0:-1]
        slide_tile = resize(slide_tile, (tile_size, tile_size), preserve_range=True)

        x = int(x / crop_scale)
        y = int(y / crop_scale)
        return [slide_tile, (x, y)]

    def slide_size(self):
        return self.slide.level_dimensions[self.level]


class SlideCrop:
    def __init__(self, slide_path, crop_scale=4, tile_size=512, overlap_ratio=0.25):
        self.tile_size = tile_size
        self.crop_scale = crop_scale
        self.crop_size = self.tile_size * self.crop_scale
        self.overlap_size = self.crop_size * overlap_ratio
        self.slide_path = slide_path
        self.slide = _SlideActor.remote(slide_path=self.slide_path)
        self.slide_batch_split = 32

    def original_slide_size(self):
        slide_width, slide_height = ray.get(self.slide.slide_size.remote())

        return [slide_height, slide_width]

    def predicted_slide_size(self):
        slide_height, slide_width = self.original_slide_size()

        return [int(slide_height / self.crop_scale), int(slide_width / self.crop_scale), ]

    def crop(self, batch_size):
        slide_width, slide_height = self.original_slide_size()

        non_overlap_size = self.crop_size - self.overlap_size
        num_slide_width = int(slide_width // non_overlap_size)
        num_slide_height = int(slide_height // non_overlap_size)

        crop_coordinates_all = []
        for i in range(num_slide_height + 2):
            for j in range(num_slide_width + 2):
                y = max(i * non_overlap_size, 0)
                x = max(j * non_overlap_size, 0)
                y = max(y, 0)
                x = max(x, 0)

                if y + self.crop_size > slide_height:
                    y = slide_height - self.crop_size
                if x + self.crop_size > slide_width:
                    x = slide_width - self.crop_size

                crop_coordinates_all.append((y, x))

        crop_coordinates_batches = [crop_coordinates_all[batch_idx:batch_idx+batch_size]
                                    for batch_idx in range(0, len(crop_coordinates_all), batch_size)]

        return self._crop_batch_split(crop_coordinates_batches=crop_coordinates_batches), len(crop_coordinates_batches)
    
    def _crop_batch_split(self, crop_coordinates_batches):
        slide_actor = None
        results = []

        for idx, crop_coordinates_batch in enumerate(crop_coordinates_batches):
            if idx % self.slide_batch_split == 0:
                if len(results) != 0:
                    yield results

                results = []
                slide_actor = _SlideActor.remote(slide_path=self.slide_path)

            results.append([slide_actor.crop.remote(crop_coordinate=crop_coordinate,
                                                    crop_size=self.crop_size,
                                                    tile_size=self.tile_size,
                                                    crop_scale=self.crop_scale)
                            for crop_coordinate in crop_coordinates_batch])

        yield results
