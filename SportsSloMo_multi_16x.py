import os
import sys
import cv2
import math
import torch
import argparse
import numpy as np
from torch.nn import functional as F
from train_log.RIFE_HDv3 import Model
from skimage.color import rgb2yuv
import lpips
from torchvision import transforms
from PIL import Image

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--modelDir', type=str, default='train_log', 
                      help='directory containing model checkpoint')
    return parser.parse_args()

# Set device
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Initialize model
args = parse_args()
model = Model()
model.load_model(args.modelDir, -1)
model.eval()
model.device()
print("Loaded v3.x HD model.")

# Define dataset directory and dimensions
BASE_DIR = '/scratch/rrm9598/hpml/acv/SportsSloMo/SportsSloMo_frames/'
FRAME_HEIGHT = 720
FRAME_WIDTH = 1280
# CLIP_START = 6235
# CLIP_END = 6285
CLIP_START = 7235
CLIP_END = 7443

loss_fn_alex = lpips.LPIPS(net='alex')

def load_frame(path):
    """Load and preprocess a frame."""
    frame = cv2.imread(path)
    if frame is None:
        raise ValueError(f"Failed to load frame: {path}")
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return frame

def inference(I0, I1, pad, multi=2, arbitrary=True):
    """Run inference with the RIFE model."""
    img = [I0, I1]
    if not arbitrary:
        for i in range(multi):
            res = [I0]
            for j in range(len(img) - 1):
                res.append(model.inference(img[j], img[j + 1]))
                res.append(img[j + 1])
            img = res
    else:
        img = [I0]
        p = 2**multi
        for i in range(p-1):
            img.append(model.inference(I0, I1))
        img.append(I1)
    
    for i in range(len(img)):
        img[i] = img[i][0][:, pad: -pad]
    return img[1: -1]

def calculate_psnr(pred_frame, gt_frame):
    """Calculate PSNR between predicted and ground truth frames."""
    pred_yuv = rgb2yuv(pred_frame / 255.)[:, :, 0] * 255
    gt_yuv = rgb2yuv(gt_frame / 255.)[:, :, 0] * 255
    diff_yuv = 128.0 + gt_yuv - pred_yuv
    mse = np.mean((diff_yuv - 128.0) ** 2)
    PIXEL_MAX = 255.0
    return 20 * math.log10(PIXEL_MAX / math.sqrt(mse))

def load_image_lpips(image_path):
    # img = Image.open(image_path).convert('RGB')
    img = Image.fromarray(image_path)
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])
    ])
    return transform(img).unsqueeze(0)

def lpips(img0, img1):
    img0 = load_image_lpips(img0)
    img1 = load_image_lpips(img1)
    return loss_fn_alex(img0, img1).item()

def benchmark_clip(clip_number):
    """Benchmark a single clip."""
    clip_dir = os.path.join(BASE_DIR, f'clip_{clip_number}')
    if not os.path.exists(clip_dir):
        print(f"Skipping non-existent clip: {clip_dir}")
        return None
    
    psnr_list = []
    lpips_list = []
    frame_files = sorted(os.listdir(clip_dir))
    
    # Process frames with stride 4
    for i in range(0, len(frame_files)-16, 16):
        # Load frames
        try:
            frame1 = load_frame(os.path.join(clip_dir, frame_files[i]))
            frame2 = load_frame(os.path.join(clip_dir, frame_files[i+16]))
            gt_frames = [load_frame(os.path.join(clip_dir, frame_files[i+j])) for j in range(1, 16)]
        except (ValueError, IndexError) as e:
            print(f"Error loading frames in clip {clip_number}, frames {i}-{i+8}: {e}")
            continue

        # Prepare input tensors
        I0 = torch.from_numpy(np.transpose(frame1, (2,0,1)).astype("float32") / 255.).cuda().unsqueeze(0)
        I1 = torch.from_numpy(np.transpose(frame2, (2,0,1)).astype("float32") / 255.).cuda().unsqueeze(0)
        
        # Add padding
        pad = 24  # For 720p resolution
        pader = torch.nn.ReplicationPad2d([0, 0, pad, pad])
        I0 = pader(I0)
        I1 = pader(I1)
        
        # Run inference
        with torch.no_grad():
            pred_frames = inference(I0, I1, pad)
        
        # Calculate PSNR for each predicted frame
        for pred, gt in zip(pred_frames, gt_frames):
            pred_np = (np.round(pred.detach().cpu().numpy().transpose(1, 2, 0) * 255)).astype('uint8')
            psnr = calculate_psnr(pred_np, gt)
            psnr_list.append(psnr)
            lpips_list.append(lpips(pred_np, gt))
    
    return np.mean(psnr_list) if psnr_list else None, np.mean(lpips_list) if lpips_list else None

def main():
    """Main function to run benchmarking across all clips."""
    results_psnr = []
    results_lpips = []
    
    for clip_num in range(CLIP_START, CLIP_END + 1):
        print(f"Processing clip {clip_num}...")
        avg_psnr, avg_lpips = benchmark_clip(clip_num)
        if avg_psnr is not None:
            results_psnr.append(avg_psnr)
            if avg_lpips is not None:
                results_lpips.append(avg_lpips)
                print(f"Clip {clip_num} Average PSNR: {avg_psnr:.2f}, Average LPIPS: {avg_lpips:.2f}")
    
    if results:
        overall_psnr = np.mean(results_psnr)
        overall_lpips = np.mean(results_lpips)
        print(f"\nOverall Average PSNR across all clips: {overall_psnr:.2f}")
        print(f"\nOverall Average LPIPS across all clips: {overall_lpips:.2f}")
        print(f"Number of clips processed: {len(results_psnr)}")
    else:
        print("No valid results obtained")

if __name__ == "__main__":
    main()
