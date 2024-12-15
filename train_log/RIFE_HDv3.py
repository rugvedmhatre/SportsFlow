import torch
import torch.nn as nn
import numpy as np
from torch.optim import AdamW
import torch.optim as optim
import itertools
from model.warplayer import warp
from torch.nn.parallel import DistributedDataParallel as DDP
from train_log.IFNet_HDv3 import *
import torch.nn.functional as F
from model.loss import *
# from model.laplacian import *
from model.heatmap_loss import HeatmapInfer

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

class JointsMSELoss(nn.Module):
    """MSE loss for heatmaps.

    Args:
        use_target_weight (bool): Option to use weighted MSE loss.
            Different joint types may have different target weights.
        loss_weight (float): Weight of the loss. Default: 1.0.
    """

    def __init__(self, use_target_weight=False):
        super().__init__()
        self.criterion = nn.MSELoss(reduce=False)
        self.use_target_weight = use_target_weight

    def forward(self, output, target):
        """Forward function."""
        batch_size = output.size(0)
        num_joints = output.size(1)

        heatmaps_pred = output.reshape(
            (batch_size, num_joints, -1)).split(1, 1)
        heatmaps_gt = target.reshape((batch_size, num_joints, -1)).split(1, 1)

        loss = 0.

        for idx in range(num_joints):
            heatmap_pred = heatmaps_pred[idx].squeeze(1)
            heatmap_gt = heatmaps_gt[idx].squeeze(1)
            loss += self.criterion(heatmap_pred, heatmap_gt)

        return loss / num_joints
    
class Model:
    def __init__(self, local_rank=-1):
        self.flownet = IFNet()
        self.device()
        self.optimG = AdamW(self.flownet.parameters(), lr=1e-6, weight_decay=1e-4)
        self.epe = EPE()
        self.version = 4.8
        # self.laploss = LapLoss()
        self.vgg = VGGPerceptualLoss().to(device)
        self.sobel = SOBEL()
        self.HeatmapInfer = HeatmapInfer()
        self.heatmaploss = JointsMSELoss()
        
        if local_rank != -1:
            self.flownet = DDP(self.flownet, device_ids=[local_rank], output_device=local_rank)

    def train(self):
        self.flownet.train()

    def eval(self):
        self.flownet.eval()

    def device(self):
        self.flownet.to(device)

    def load_model(self, path, rank=0):
        def convert(param):
            if rank == -1:
                return {
                    k.replace("module.", ""): v
                    for k, v in param.items()
                    if "module." in k
                }
            else:
                return param
        if rank <= 0:
            if torch.cuda.is_available():
                self.flownet.load_state_dict(convert(torch.load('{}/flownet.pkl'.format(path))), False)
            else:
                self.flownet.load_state_dict(convert(torch.load('{}/flownet.pkl'.format(path), map_location ='cpu')), False)
        
    def save_model(self, path, rank=0):
        if rank == 0:
            torch.save(self.flownet.state_dict(),'{}/flownet.pkl'.format(path))

    def inference(self, img0, img1, timestep=0.5, scale=1.0):
        imgs = torch.cat((img0, img1), 1)
        scale_list = [8/scale, 4/scale, 2/scale, 1/scale]
        flow, mask, merged = self.flownet(imgs, timestep, scale_list)
        return merged[3]
    
    def update(self, imgs, gt, learning_rate=0, mul=1, training=True, flow_gt=None):
        for param_group in self.optimG.param_groups:
            param_group['lr'] = learning_rate
        img0 = imgs[:, :3]
        img1 = imgs[:, 3:]
        if training:
            self.train()
        else:
            self.eval()
        scale = [8, 4, 2, 1]
        # flow, mask, merged = self.flownet(torch.cat((imgs, gt), 1), scale=scale, training=training)
        flow, mask, merged = self.flownet(torch.cat((imgs, gt), 1), training=training)
        loss_l1 = (merged[3] - gt).abs().mean()
        loss_smooth = self.sobel(flow[3], flow[3]*0).mean()
        predicted_kpt, gt_kpt = self.HeatmapInfer(merged[3], gt)
        loss_kpt = 0.1 * self.heatmaploss(predicted_kpt, gt_kpt).mean()
        # loss_lap = self.laploss(merged[3], gt)
        loss_vgg = self.vgg(merged[3], gt)
        if training:
            self.optimG.zero_grad()
            # loss_G = loss_l1 + loss_cons + loss_smooth * 0.1
            # loss_G = loss_l1 + loss_smooth * 0.1 + loss_lap + loss_vgg
            loss_G = loss_l1 + loss_smooth * 0.1 + loss_vgg + loss_kpt
            loss_G.backward()
            self.optimG.step()
        else:
            flow_teacher = flow[2]
        return merged[3], {
            'mask': mask,
            'flow': flow[3][:, :2],
            'loss_l1': loss_l1,
            # 'loss_cons': loss_cons,
            'loss_vgg': loss_vgg,
            # 'loss_lap' : loss_lap,
            'loss_kpt' : loss_kpt,
            'loss_smooth': loss_smooth,
            }
