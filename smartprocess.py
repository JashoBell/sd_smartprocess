import math
import os
import sys

import PIL
import numpy as np
import tqdm
from PIL import Image, ImageOps

from clipcrop import CropClip
import reallysafe
from modules import shared, images, safe
import modules.gfpgan_model
import modules.codeformer_model
from modules.shared import opts, cmd_opts

if cmd_opts.deepdanbooru:
    import modules.deepbooru as deepbooru


def interrogate_image(image: Image, full=False):
    if not full:
        prev_artists = shared.opts.interrogate_use_builtin_artists
        prev_max = shared.opts.interrogate_clip_max_length
        prev_min = shared.opts.interrogate_clip_min_length
        shared.opts.interrogate_clip_min_length = 10
        shared.opts.interrogate_clip_max_length = 20
        shared.opts.interrogate_use_builtin_artists = False
        caption = shared.interrogator.interrogate(image)
        shared.opts.interrogate_clip_min_length = prev_min
        shared.opts.interrogate_clip_max_length = prev_max
        shared.opts.interrogate_use_builtin_artists = prev_artists
    else:
        caption = shared.interrogator.interrogate(image)

    return caption


def preprocess(src,
               dst,
               pad,
               crop,
               width,
               append_filename,
               save_txt,
               pretxt_action,
               flip,
               split,
               caption,
               caption_length,
               caption_deepbooru,
               split_threshold,
               overlap_ratio,
               subject_class,
               subject,
               replace_class,
               restore_faces,
               face_model,
               upscale,
               upscale_ratio,
               scaler
               ):
    try:
        if pad and crop:
            crop = False
        shared.state.textinfo = "Loading models for smart processing..."
        safe.RestrictedUnpickler = reallysafe.RestrictedUnpickler
        if caption:
            shared.interrogator.load()

        if caption_deepbooru:
            deepbooru.model.start()

        prework(src,
                dst,
                pad,
                crop,
                width,
                append_filename,
                save_txt,
                pretxt_action,
                flip,
                split,
                caption,
                caption_length,
                caption_deepbooru,
                split_threshold,
                overlap_ratio,
                subject_class,
                subject,
                replace_class,
                restore_faces,
                face_model,
                upscale,
                upscale_ratio,
                scaler)

    finally:

        if caption:
            shared.interrogator.send_blip_to_ram()

        if caption_deepbooru:
            deepbooru.model.stop()

    return "Processing complete.", ""


def prework(src,
            dst,
            pad_image,
            crop_image,
            width,
            append_filename,
            save_txt,
            pretxt_action,
            flip,
            split,
            caption_image,
            caption_length,
            caption_deepbooru,
            split_threshold,
            overlap_ratio,
            subject_class,
            subject,
            replace_class,
            restore_faces,
            face_model,
            upscale,
            upscale_ratio,
            scaler):
    try:
        del sys.modules['models']
    except:
        pass
    width = width
    height = width
    src = os.path.abspath(src)
    dst = os.path.abspath(dst)

    if not crop_image and not caption_image and not restore_faces and not upscale and not pad_image:
        print("Nothing to do.")
        shared.state.textinfo = "Nothing to do!"
        return

    assert src != dst, 'same directory specified as source and destination'

    os.makedirs(dst, exist_ok=True)

    files = os.listdir(src)

    shared.state.textinfo = "Preprocessing..."
    shared.state.job_count = len(files)

    def build_caption(image):
        existing_caption = None
        if not append_filename:
            existing_caption_filename = os.path.splitext(filename)[0] + '.txt'
            if os.path.exists(existing_caption_filename):
                with open(existing_caption_filename, 'r', encoding="utf8") as file:
                    existing_caption = file.read()
        else:
            existing_caption = ''.join(c for c in filename if c.isalpha() or c in [" ", ","])

        caption = ""
        if caption_image:
            caption = interrogate_image(img, True)

        if caption_deepbooru:
            if len(caption) > 0:
                caption += ", "
            caption += deepbooru.model.tag_multi(image)

        if pretxt_action == 'prepend' and existing_caption:
            caption = existing_caption + ' ' + caption
        elif pretxt_action == 'append' and existing_caption:
            caption = caption + ' ' + existing_caption
        elif pretxt_action == 'copy' and existing_caption:
            caption = existing_caption

        caption = caption.strip()
        if replace_class and subject is not None and subject_class is not None:
            # Find and replace "a SUBJECT CLASS" in caption with subject name
            split_class = subject_class.split(", ")
            for class_name in split_class:
                if f"a {class_name}" in caption:
                    caption = caption.replace(f"a {class_name}", subject)
                if class_name in caption:
                    caption = caption.replace(class_name, subject)

        if 0 < caption_length < len(caption):
            split_cap = caption.split(" ")
            caption = ""
            cap_test = ""
            split_idx = 0
            while True and split_idx < len(split_cap):
                cap_test += f" {split_cap[split_idx]}"
                if len(cap_test) < caption_length:
                    caption = cap_test
                split_idx += 1

        caption = caption.strip()
        return caption

    def save_pic_with_caption(image, img_index, existing_caption):

        if append_filename:
            filename_part = existing_caption
            basename = f"{img_index:05}-{subindex[0]}-{filename_part}"
        else:
            basename = f"{img_index:05}-{subindex[0]}"

        shared.state.current_image = img
        image.save(os.path.join(dst, f"{basename}.png"))

        if save_txt:
            if len(existing_caption) > 0:
                with open(os.path.join(dst, f"{basename}.txt"), "w", encoding="utf8") as file:
                    file.write(existing_caption)

        subindex[0] += 1

    def save_pic(image, img_index, existing_caption=None):
        save_pic_with_caption(image, img_index, existing_caption=existing_caption)

        if flip:
            save_pic_with_caption(ImageOps.mirror(image), img_index, existing_caption=existing_caption)

    def split_pic(image, img_inverse_xy):
        if img_inverse_xy:
            from_w, from_h = image.height, image.width
            to_w, to_h = height, width
        else:
            from_w, from_h = image.width, image.height
            to_w, to_h = width, height
        h = from_h * to_w // from_w
        if img_inverse_xy:
            image = image.resize((h, to_w))
        else:
            image = image.resize((to_w, h))

        split_count = math.ceil((h - to_h * overlap_ratio) / (to_h * (1.0 - overlap_ratio)))
        y_step = (h - to_h) / (split_count - 1)
        for i in range(split_count):
            y = int(y_step * i)
            if img_inverse_xy:
                split_img = image.crop((y, 0, y + to_h, to_w))
            else:
                split_img = image.crop((0, y, to_w, y + to_h))
            yield split_img

    crop_clip = None

    if crop_image:
        split_threshold = max(0.0, min(1.0, split_threshold))
        overlap_ratio = max(0.0, min(0.9, overlap_ratio))
        crop_clip = CropClip()

    for index, imagefile in enumerate(tqdm.tqdm(files)):

        if shared.state.interrupted:
            break

        subindex = [0]
        filename = os.path.join(src, imagefile)
        try:
            img = Image.open(filename).convert("RGB")
        except Exception:
            continue

        shared.state.textinfo = f"Processing: '({filename})"
        if crop_image:
            # Interrogate once
            short_caption = interrogate_image(img)

            if subject_class is not None and subject_class != "":
                short_caption = subject_class

            shared.state.textinfo = f"Cropping: {short_caption}"
            if img.height > img.width:
                ratio = (img.width * height) / (img.height * width)
                inverse_xy = False
            else:
                ratio = (img.height * width) / (img.width * height)
                inverse_xy = True

            if split and ratio < 1.0 and ratio <= split_threshold:
                for splitted in split_pic(img, inverse_xy):
                    # Build our caption
                    full_caption = None
                    if caption_image:
                        full_caption = build_caption(splitted)
                    save_pic(splitted, index, existing_caption=full_caption)

            src_ratio = img.width / img.height
            # Pad image before cropping?
            if src_ratio != 1:
                if img.width > img.height:
                    pad_width = img.width
                    pad_height = img.width
                else:
                    pad_width = img.height
                    pad_height = img.height
                res = Image.new("RGB", (pad_width, pad_height))
                res.paste(img, box=(pad_width // 2 - img.width // 2, pad_height // 2 - img.height // 2))
                img = res

            im_data = crop_clip.get_center(img, prompt=short_caption)
            crop_width = im_data[1] - im_data[0]
            center_x = im_data[0] + (crop_width / 2)
            crop_height = im_data[3] - im_data[2]
            center_y = im_data[2] + (crop_height / 2)
            crop_ratio = crop_width / crop_height
            dest_ratio = width / height
            tgt_width = crop_width
            tgt_height = crop_height

            if crop_ratio != dest_ratio:
                if crop_width > crop_height:
                    tgt_height = crop_width / dest_ratio
                    tgt_width = crop_width
                else:
                    tgt_width = crop_height / dest_ratio
                    tgt_height = crop_height

                # Reverse the above if dest is too big
                if tgt_width > img.width or tgt_height > img.height:
                    if tgt_width > img.width:
                        tgt_width = img.width
                        tgt_height = tgt_width / dest_ratio
                    else:
                        tgt_height = img.height
                        tgt_width = tgt_height / dest_ratio

            tgt_height = int(tgt_height)
            tgt_width = int(tgt_width)
            left = max(center_x - (tgt_width / 2), 0)
            right = min(center_x + (tgt_width / 2), img.width)
            top = max(center_y - (tgt_height / 2), 0)
            bottom = min(center_y + (tgt_height / 2), img.height)
            img = img.crop((left, top, right, bottom))
            default_resize = True
            shared.state.current_image = img
        else:
            default_resize = False

        if restore_faces:
            shared.state.textinfo = f"Restoring faces using {face_model}..."
            if face_model == "gfpgan":
                restored_img = modules.gfpgan_model.gfpgan_fix_faces(np.array(img, dtype=np.uint8))
                img = Image.fromarray(restored_img)
            else:
                restored_img = modules.codeformer_model.codeformer.restore(np.array(img, dtype=np.uint8),
                                                                           w=1.0)
                img = Image.fromarray(restored_img)
            shared.state.current_image = img

        if upscale:
            shared.state.textinfo = "Upscaling..."
            upscaler = shared.sd_upscalers[scaler]
            res = upscaler.scaler.upscale(img, upscale_ratio, upscaler.data_path)
            img = res
            default_resize = True
            shared.state.current_image = img

        if pad_image:
            ratio = width / height
            src_ratio = img.width / img.height

            src_w = width if ratio < src_ratio else img.width * height // img.height
            src_h = height if ratio >= src_ratio else img.height * width // img.width

            resized = images.resize_image(0, img, src_w, src_h)
            res = Image.new("RGB", (width, height))
            res.paste(resized, box=(width // 2 - src_w // 2, height // 2 - src_h // 2))
            img = res

        if default_resize:
            img = images.resize_image(1, img, width, height)
        shared.state.current_image = img
        full_caption = build_caption(img)
        save_pic(img, index, existing_caption=full_caption)

        shared.state.nextjob()
