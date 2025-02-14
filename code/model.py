import segmentation_models_pytorch
import torch
import torch.nn as nn
import torch.nn.functional as F
from models.uresnet import get_segmentation_model, batch_norm_to_group_norm
import torchvision

def get_model(modelname, inchannels=12, pretrained=True):

    if modelname == "vit":
        from models.vits import VisionTransformer, vit_base, vit_tiny, vit_small
        #model = VisionTransformer(img_size = [224], patch_size = 16, in_chans = 3, num_classes = 0, embed_dim = 768, depth = 12,
        #num_heads = 12, mlp_ratio = 4., qkv_bias = False, qk_scale = None, drop_rate = 0., attn_drop_rate = 0.,
        #drop_path_rate = 0., norm_layer = nn.LayerNorm)
        #model = vit_base()

        #state_dict = torch.hub.load_state_dict_from_url(
        #    url="https://dl.fbaipublicfiles.com/dino/dino_vitbase16_pretrain/dino_vitbase16_pretrain.pth",
        #    map_location="cpu",
        #)

        model = vit_small(patch_size=8)
        state_dict = torch.hub.load_state_dict_from_url(
            url="https://dl.fbaipublicfiles.com/dino/dino_deitsmall8_pretrain/dino_deitsmall8_pretrain.pth",
            map_location="cpu",
        )
        model.load_state_dict(state_dict, strict=False)

        model.patch_embed.proj = nn.Conv2d(12, 384, kernel_size=(8, 8), stride=(8, 8))
        #print()

    elif modelname == "unetvit":
        from models.unetvit import vit_small
        model = vit_small(patch_size=8)
        state_dict = torch.hub.load_state_dict_from_url(
            url="https://dl.fbaipublicfiles.com/dino/dino_deitsmall8_pretrain/dino_deitsmall8_pretrain.pth",
            map_location="cpu",
        )
        model.load_state_dict(state_dict, strict=False)

    elif modelname == "prototypevit":
        from models.prototypevit import vit_small
        model = vit_small(patch_size=8)
        state_dict = torch.hub.load_state_dict_from_url(
            url="https://dl.fbaipublicfiles.com/dino/dino_deitsmall8_pretrain/dino_deitsmall8_pretrain.pth",
            map_location="cpu",
        )
        model.load_state_dict(state_dict, strict=False)

    elif modelname == "unet":
        # initialize model (random weights)
        model = UNet(n_channels=inchannels,
                     n_classes=1,
                     bilinear=False)
    elif modelname == "fcnresnet":

        backbone = torchvision.models.resnet50(pretrained=pretrained)
        state_dict = backbone.state_dict()
        state_dict.pop("fc.bias")
        state_dict.pop("fc.weight")
        model = torchvision.models.segmentation.fcn_resnet50(num_classes=1)
        model.backbone.load_state_dict(state_dict)

        model.backbone.conv1 = nn.Conv2d(12, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=True)

        # rename forward function
        model.forward_dict = model.forward
        # reassign forward without the dictionary output.
        model.forward = lambda x: model.forward_dict(x)["out"]




    elif modelname == "uresnet":
        # initialize model (random weights)

        backbone = torchvision.models.resnet18(pretrained=pretrained)
        backbone.conv1 = nn.Conv2d(12, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)

        model = get_segmentation_model(backbone, feature_indices=(0, 4, 5, 6, 7),
                                       feature_channels=(64, 64, 128, 256, 512))

    elif modelname in ["resnetunet", "resnetunetscse"]:
        import segmentation_models_pytorch as smp
        model = smp.Unet(
            encoder_name="resnet34" if "resnet" in modelname else "efficientnet-b7",  # choose encoder, e.g. mobilenet_v2 or efficientnet-b7
            encoder_weights="imagenet" if pretrained else None,
            in_channels=3,
            decoder_attention_type="scse" if modelname == "resnetunetscse" else None,
            classes=1,
        )
        model.encoder.conv1 = torch.nn.Conv2d(inchannels, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)

    elif modelname in ["manet"]:
        import segmentation_models_pytorch as smp
        model = smp.MAnet(
            encoder_name="resnet34",
            encoder_weights="imagenet" if pretrained else None,
            in_channels=3,
            classes=1,
        )
        model.encoder.conv1 = torch.nn.Conv2d(inchannels, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)
    else:
        raise ValueError(f"model {modelname} not recognized")
    return model

#============== some parts of the U-Net model ===============#
""" Parts of the U-Net model """
class DoubleConv(nn.Module):
    """(convolution => [BN] => ReLU) * 2"""

    def __init__(self, in_channels, out_channels, mid_channels=None):
        super().__init__()
        if not mid_channels:
            mid_channels = out_channels
        self.double_conv = nn.Sequential(
            nn.Conv2d(in_channels, mid_channels, kernel_size=3, padding=1),
            nn.BatchNorm2d(mid_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(mid_channels, out_channels, kernel_size=3, padding=1),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True)
        )

    def forward(self, x):
        return self.double_conv(x)


class Down(nn.Module):
    """Downscaling with maxpool then double conv"""

    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.maxpool_conv = nn.Sequential(
            nn.MaxPool2d(2),
            DoubleConv(in_channels, out_channels)
        )

    def forward(self, x):
        return self.maxpool_conv(x)


class Up(nn.Module):
    """Upscaling then double conv"""

    def __init__(self, in_channels, out_channels, bilinear=True):
        super().__init__()

        # if bilinear, use the normal convolutions to reduce the number of channels
        if bilinear:
            self.up = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True)
            self.conv = DoubleConv(in_channels, out_channels, in_channels // 2)
        else:
            self.up = nn.ConvTranspose2d(in_channels , in_channels // 2, kernel_size=2, stride=2)
            self.conv = DoubleConv(in_channels, out_channels)


    def forward(self, x1, x2):
        x1 = self.up(x1)
        # input is CHW
        diffY = x2.size()[2] - x1.size()[2]
        diffX = x2.size()[3] - x1.size()[3]

        x1 = F.pad(x1, [diffX // 2, diffX - diffX // 2,
                        diffY // 2, diffY - diffY // 2])
        # if you have padding issues, see
        # https://github.com/HaiyongJiang/U-Net-Pytorch-Unstructured-Buggy/commit/0e854509c2cea854e247a9c615f175f76fbb2e3a
        # https://github.com/xiaopeng-liao/Pytorch-UNet/commit/8ebac70e633bac59fc22bb5195e513d5832fb3bd
        x = torch.cat([x2, x1], dim=1)
        return self.conv(x)


class OutConv(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(OutConv, self).__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size=1)

    def forward(self, x):
        return self.conv(x)

#=================== Assembling parts to form the network =================#
""" Full assembly of the parts to form the complete network """

class UNet(nn.Module):
    def __init__(self, n_channels, n_classes, bilinear=True):
        super(UNet, self).__init__()
        self.n_channels = n_channels
        self.n_classes = n_classes
        self.bilinear = bilinear

        self.inc = DoubleConv(n_channels, 64)
        self.down1 = Down(64, 128)
        self.down2 = Down(128, 256)
        self.down3 = Down(256, 512)
        factor = 2 if bilinear else 1
        self.down4 = Down(512, 1024 // factor)
        self.up1 = Up(1024, 512 // factor, bilinear)
        self.up2 = Up(512, 256 // factor, bilinear)
        self.up3 = Up(256, 128 // factor, bilinear)
        self.up4 = Up(128, 64, bilinear)
        self.outc = OutConv(64, n_classes)

    def forward(self, x):
        x1 = self.inc(x.float())
        x2 = self.down1(x1)
        x3 = self.down2(x2)
        x4 = self.down3(x3)
        x5 = self.down4(x4)
        x = self.up1(x5, x4)
        x = self.up2(x, x3)
        x = self.up3(x, x2)
        x = self.up4(x, x1)
        logits = self.outc(x)
        return logits
