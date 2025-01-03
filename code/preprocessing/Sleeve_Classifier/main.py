import numpy as np
import torch
import torch.nn as nn
from torch.autograd import Variable
import glob
from random import shuffle
import time
import os
from tqdm import tqdm
import argparse
from torch.utils.data import DataLoader
# from dataLoader import Sleeve_Dataset
from preprocessing.Sleeve_Classifier.dataLoader import Sleeve_Dataset
import torchvision.models as models
import cv2
import json

full_weight_name = 'sleeve_clf_Adam'


class classifier(nn.Module):
    def __init__(self):
        super(classifier, self).__init__()
        ResEncoder = models.resnet18(pretrained=True)
        self.backbone = torch.nn.Sequential(*(list(ResEncoder.children())[:-1])).cuda()

        self.cf = nn.Sequential(
            nn.Linear(512, 256),
            nn.ReLU(inplace=True),
            nn.Dropout(p=0.5),
            nn.Linear(256, 256),
            nn.ReLU(inplace=True),
            nn.Dropout(p=0.5),
            nn.Linear(256, 2),
        )

    def weights_init(self):
        for m in self.modules():
            if isinstance(m, (nn.Conv2d, nn.Linear, nn.Conv3d)):
                nn.init.xavier_normal_(m.weight)
            elif isinstance(m, (nn.BatchNorm2d, nn.BatchNorm3d)):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

    def forward(self, x):
        # Get input size
        x = self.backbone(x)

        b, c, h, w = x.shape
        x = x.reshape(b, -1)
        pred = self.cf(x)

        return pred


def train(opt):
    from torch.utils.tensorboard import SummaryWriter
    writer = SummaryWriter(os.path.join('runs/{}'.format(full_weight_name)))

    os.makedirs('weights', exist_ok=True)
    dataloader_train = DataLoader(dataset=Sleeve_Dataset(), batch_size=opt.batch_size, num_workers=11, drop_last=True, shuffle=True)

    # Create model
    model = classifier().cuda()

    optimizer = torch.optim.Adam(model.parameters(), lr=opt.lr)
    # optimizer = torch.optim.SGD(model.parameters(), lr=opt.lr, momentum=0.9, weight_decay=0.00001)
    optimizer.zero_grad()

    # scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=50, gamma=0.5)
    # scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=[10*i for i in range(1, 11)], gamma=0.5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=50, eta_min=1e-6)

    # Load pretrained weights
    if os.path.isfile(opt.weights_path):
        print("=> loading checkpoint '{}'".format(opt.weights_path))
        checkpoint = torch.load(opt.weights_path)
        start_epoch = checkpoint['epoch']
        model.load_state_dict(checkpoint['state_dict'])
        optimizer.load_state_dict(checkpoint['optimizer'])
        scheduler.load_state_dict(checkpoint['scheduler'])

        print("=> loaded checkpoint '{}' (epoch {})"
              .format(opt.weights_path, checkpoint['epoch']))
    else:
        start_epoch = 0
        print("=> no checkpoint found at '{}'".format(opt.weights_path))

    criterion_CE = nn.CrossEntropyLoss().cuda()
    model.train()


    # Start training
    for epoch_index in range(start_epoch, opt.n_epochs):

        print('epoch_index=', epoch_index)

        for param_group in optimizer.param_groups:
            print('lr: ' + str(param_group['lr']))

        start = time.time()


        # in each minibatch
        pbar = tqdm(dataloader_train, desc='training')

        for batchIdx, (imgs, label) in enumerate(pbar):

            imgs = Variable(imgs.cuda())
            label = Variable(label.cuda())
            if opt.vis:
                img_array = imgs[0].permute(1,2,0).detach().cpu().numpy()[:,:,::-1]
                img_array = cv2.normalize(img_array,  None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
                cv2.imshow('img', img_array)
                cv2.waitKey(0)

            prediction = model(imgs)

            # Train with Source
            loss = criterion_CE(prediction, label)

            pbar.set_description("Loss: {}".format(loss.item()))   
            writer.add_scalar("CE_loss", loss, len(pbar)*epoch_index + batchIdx)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        scheduler.step()
        endl = time.time()
        print('Costing time:', (endl-start)/60)
        t = time.localtime()
        current_time = time.strftime("%H:%M:%S", t)
        print(current_time)
        save_info = {
            'epoch': epoch_index + 1,
            'state_dict': model.state_dict(),
            'optimizer': optimizer.state_dict(),
            'scheduler': scheduler.state_dict(),
        }
        if epoch_index % 5 == 4:
            weight_name = '{}_{}.pkl'.format(opt.weights_path.split('.')[0], epoch_index + 1)
            torch.save(save_info, weight_name)
        torch.save(save_info, opt.weights_path)


def val(opt):
    # Create model
    model = classifier().cuda().eval()
    best_score = 0
    best_epoch = 0
    for e in range(25, 30, 5):
        weight_name = '{}_{}.pkl'.format(opt.weights_path.split('.')[0], e)
        if not os.path.isfile(weight_name):
            break
        checkpoint = torch.load(weight_name)
        model.load_state_dict(checkpoint['state_dict'])
        # Original validation Set
        dataloader_test = DataLoader(dataset=Sleeve_Dataset(mode=opt.mode), batch_size=1, num_workers=11, drop_last=True, shuffle=True)
            
        pbar = tqdm(dataloader_test, desc='testing')
        
        correct = 0

        for batchIdx, (imgs, label) in enumerate(pbar):
            imgs = Variable(imgs.cuda())
            label = Variable(label.cuda())
            prediction = model(imgs)
            prediction = prediction.argmax(dim=1, keepdim=True) 
            correct += prediction.eq(label.view_as(prediction)).sum().item()
            if prediction.eq(label.view_as(prediction)).sum().item() == 0 and opt.vis:
                print(prediction.item(), label.item())
                img_array = imgs[0].permute(1,2,0).detach().cpu().numpy()[:,:,::-1]
                img_array = cv2.normalize(img_array,  None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
                cv2.imshow('img', img_array)
                cv2.waitKey(0)
            # success_dict[label.item()] = success_dict.get(label.item(), 0) + prediction.eq(label.view_as(prediction)).sum().item()
        print("Epoch {}".format(e))
        print('\nAccuracy validation: {}/{} ({:.0f}%)\n'.format(correct, len(dataloader_test.dataset),
            100. * correct / len(dataloader_test.dataset)))

        if correct > best_score:
            best_score, best_epoch = correct, e

    print("The best epoch is {} with accuracy {}%".format(best_epoch, 100. * best_score / len(dataloader_test.dataset)))

def test(opt):
    os.makedirs(opt.output_dir, exist_ok=True)
    # Create model
    model = classifier().cuda().eval()
    weight_name = '{}_{}.pkl'.format(opt.weights_path.split('.')[0], 25)
    checkpoint = torch.load(weight_name)
    # checkpoint = torch.load(opt.weights_path)
    model.load_state_dict(checkpoint['state_dict'])
    # Original validation Set
    dataloader_test = DataLoader(dataset=Sleeve_Dataset(mode=opt.mode, path=opt.input_dir), batch_size=1, num_workers=11, drop_last=True, shuffle=True)
        
    pbar = tqdm(dataloader_test, desc='testing')
    correct = 0
    # success_dict = {}

    for batchIdx, (imgs, imgNames) in enumerate(pbar):
        imgs = Variable(imgs.cuda())
        prediction = model(imgs)
        prediction = prediction.argmax(dim=1, keepdim=True) 
        # 0 is sleeve, 1 is sleeveless
        info_format = {
            "sleeve_type": prediction.reshape(-1).tolist(),
            "product_type": "top",
        }
        # print(pose_format)
        save_info_name = os.path.join(opt.output_dir, '{}.json'.format(imgNames[0]))
        if os.path.isfile(save_info_name):
            with open(save_info_name, 'r') as f:
                product_info = json.load(f)
            product_info.update(info_format)
            with open(save_info_name, 'w') as f:
                json.dump(product_info, f)

        else:
            with open(save_info_name, 'w') as f:
                json.dump(info_format, f)

def main():
    start_time = time.time()
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode",
                        type=str,
                        choices=['train', 'val', 'test', 'preprocess'],
                        default='train',
                        # required=True,
                        help="operation mode")
    parser.add_argument("--weights_path",
                        type=str,
                        default='weights/{}.pkl'.format(full_weight_name),
                        help="model path for inference")
    parser.add_argument("--n_epochs",
                        type=int,
                        default=50,
                        help="number of epochs of training")
    parser.add_argument("--batch_size",
                        type=int,
                        default=32,
                        help="size of the batches")
    parser.add_argument("--lr",
                        type=float,
                        default=1e-4,
                        # default=0.016,
                        help="adam: learning rate")
    parser.add_argument("--input_dir", type=str)
    parser.add_argument("--output_dir", type=str)
    parser.add_argument("--brand", type=str)
    parser.add_argument("--vis", type=bool, default=False)
    parser.add_argument("--cat", type=str, default=None)

    opt = parser.parse_args()
    print(opt)

    if opt.mode == 'train':
        train(opt)
    elif opt.mode == 'val':
        val(opt)
    elif opt.mode == 'preprocess':
        import time
        opt.mode = 'test'
        file_path = os.path.abspath(__file__)
        code_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(file_path))))
        Data_path = os.path.join(code_path, 'Data')
        data_folder = os.path.join(Data_path, 'parse_filtered_Data', opt.brand)
        # data_folder = os.path.join('../parse_filtered_Data', opt.brand)
        cats = [opt.cat] if opt.cat else [os.path.basename(cat) for cat in glob.glob(os.path.join(data_folder, '*'))]
        for cat in cats:
            print(cat)
            cat_folder = os.path.join(data_folder, cat)
            opt.input_dir = os.path.join(cat_folder, 'product')
            opt.output_dir = os.path.join(cat_folder, 'product_info')
            test(opt)
        print("Product Classification Time {:.4f}".format(time.time() - start_time))
    else:
        test(opt)

def sleeve_classifier_py(mode, brand):
    import time
    start_time = time.time()
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode",
                        type=str,
                        choices=['train', 'val', 'test', 'preprocess'],
                        default=mode,
                        # required=True,
                        help="operation mode")
    parser.add_argument("--weights_path",
                        type=str,
                        default='weights/{}.pkl'.format(full_weight_name),
                        help="model path for inference")
    parser.add_argument("--n_epochs",
                        type=int,
                        default=50,
                        help="number of epochs of training")
    parser.add_argument("--batch_size",
                        type=int,
                        default=32,
                        help="size of the batches")
    parser.add_argument("--lr",
                        type=float,
                        default=1e-4,
                        # default=0.016,
                        help="adam: learning rate")
    parser.add_argument("--input_dir", type=str)
    parser.add_argument("--output_dir", type=str)
    parser.add_argument("--brand", type=str, default=brand)
    parser.add_argument("--vis", type=bool, default=False)
    parser.add_argument("--cat", type=str, default=None)

    opt = parser.parse_args()
    print(opt)

    if opt.mode == 'train':
        train(opt)
    elif opt.mode == 'val':
        val(opt)
    elif opt.mode == 'preprocess':
        import time
        opt.mode = 'test'
        file_path = os.path.abspath(__file__)
        code_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(file_path))))
        Data_path = os.path.join(code_path, 'Data')
        data_folder = os.path.join(Data_path, 'parse_filtered_Data', opt.brand)
        # data_folder = os.path.join('../parse_filtered_Data', opt.brand)
        cats = [opt.cat] if opt.cat else [os.path.basename(cat) for cat in glob.glob(os.path.join(data_folder, '*'))]
        for cat in cats:
            print(cat)
            cat_folder = os.path.join(data_folder, cat)
            opt.input_dir = os.path.join(cat_folder, 'product')
            opt.output_dir = os.path.join(cat_folder, 'product_info')
            test(opt)
        print("Product Classification Time {:.4f}".format(time.time() - start_time))
    else:
        test(opt)

if __name__ == '__main__':
    main()