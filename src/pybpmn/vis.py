import functools
import logging
import math
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple
from xml.etree.ElementTree import Element

import numpy as np
import yamlu
from PIL import Image
from lxml import etree
from yamlu import img_ops
from yamlu.img import BoundingBox

from pybpmn.util import bounds_to_bb, to_int_or_float, get_omgdi_ns, parse_annotation_background_width

_logger = logging.getLogger(__name__)


class Visualizer:
    def __init__(self, bpmn_path: Path, img_path: Path, color="orange"):
        self.bpmn_path = bpmn_path
        self.img_path = img_path
        self.color = color
        self.img_id = bpmn_path.stem

    def create_bpmn_overlay_img(self):
        with tempfile.TemporaryDirectory() as tmpdirname:
            img_bpmn = self.render(png_path=Path(tmpdirname) / f"{self.img_id}.png")
        img_w = parse_annotation_background_width(self.bpmn_path)
        return self.create_overlayed_hw_img(img_bpmn, img_w=img_w)

    def create_overlayed_hw_img(self, img_bpmn: Image.Image, img_w=None, alpha=1.0) -> Image.Image:
        img_hw = yamlu.read_img(self.img_path)

        scale = img_hw.width / img_w

        # https://stackoverflow.com/questions/13027169/scale-images-with-pil-preserving-transparency-and-color
        target_size = round(img_bpmn.width * scale), round(img_bpmn.height * scale)
        # img_bpmn = img_bpmn.resize(target_size)
        bands = img_bpmn.split()
        bands = [b.resize(target_size, Image.LINEAR) for b in bands]
        img_bpmn = Image.merge("RGBA", bands)

        img_overlay = img_hw.convert("RGBA").copy()
        if self.color == "black":
            img_bpmn_transparent = img_ops.grayscale_transparency(img_bpmn)
            img_overlay.alpha_composite(img_bpmn_transparent)
        else:
            img_bpmn_transparent = img_ops.white_to_transparency(img_bpmn, thresh=200)
            img_bpmn_transparent = img_ops.black_to_color(img_bpmn_transparent, self.color)
            if alpha < 1.0:
                # noinspection PyTypeChecker
                img_np = np.asarray(img_bpmn_transparent).copy()
                img_np[..., -1] = np.round(img_np[..., -1] * alpha).astype(img_np.dtype)
                img_bpmn_transparent = Image.fromarray(img_np)
            img_overlay.paste(img_bpmn_transparent, mask=img_bpmn_transparent)

        return img_overlay

    def render(self, png_path: Path, shift_to_origin=False):
        """
        :param png_path: path where the rendered bpmn should be saved to
        :param shift_to_origin: bpmn-to-image aligns diagrams to origin, i.e. it creates an image with diagram shifted
            such that its top-left is close to (0,0), and does not render elements at exact BPMNDI positions.
            when set to False, we undo this operation.
        """
        cmd = ["bpmn-to-image", "--no-title", "--no-footer", f"{self.bpmn_path}:{png_path}"]
        _logger.debug("Executing: %s", " ".join(cmd))
        subprocess.run(cmd, check=True, capture_output=True)
        img: Image.Image = Image.open(png_path)

        if not shift_to_origin:
            #  get bounding box from xml, and revert this shifting operation
            bb = get_bpmn_bounding_box(self.bpmn_path)

            left_offset = bb.lr_mid - img.width / 2.0
            top_offset = bb.tb_mid - img.height / 2.0

            size = math.ceil(left_offset + img.width), math.ceil(top_offset + img.height)
            img_orig_size = Image.new("RGBA", size, (255, 255, 255, 0))
            img_orig_size.paste(img, box=(round(left_offset), round(top_offset)))
            img = img_orig_size

        img.save(png_path)
        # print("rendered bpmn img size:", bpmn_img.size)
        # print("actual bounding box of all bpmn symbols:", bb, "w/h:", bb.w, bb.h)
        return img


def get_bpmn_bounding_box(bpmn_path):
    document = etree.parse(str(bpmn_path))
    return get_bpmn_bounding_box_doc(document)


def get_bpmn_bounding_box_doc(document):
    """
    :return: the smallest bounding box that covers all diagram symbols
    """
    bounds, waypoints = get_bpmn_bounds_waypoints(document)

    pts = np.array([[to_int_or_float(wp.get("x")), to_int_or_float(wp.get("y"))] for wp in waypoints])
    pts_bbs = [BoundingBox.from_points(pts, allow_neg_coord=True)] if len(pts) > 0 else []

    bounds_bbs = [bounds_to_bb(bound) for bound in bounds]
    bb = functools.reduce(lambda bb1, bb2: bb1.union(bb2), bounds_bbs + pts_bbs)
    return bb


def get_bpmn_bounds_waypoints(document) -> Tuple[List[Element], List[Element]]:
    root = document.getroot()

    diagram = root.find("bpmndi:BPMNDiagram", root.nsmap)
    bounds = diagram.findall(".//omgdc:Bounds", root.nsmap)

    ns = get_omgdi_ns(diagram)
    waypoints = diagram.findall(f".//{ns}:waypoint", diagram.nsmap)

    return bounds, waypoints