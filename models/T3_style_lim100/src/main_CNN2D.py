import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.tensorboard import SummaryWriter
from torchinfo import summary
from model.Resnet import ResNetFintune
from utils.load_img import get_loader
from utils.evaluate import ConfusionMatrix, EarlyStopping
from utils.train_CNN2D import train_model, eval_model
from pathlib import Path
import numpy as np
import shutil
import datetime
import glob
import hydra

today = datetime.date.today()


@hydra.main(config_path="conf", config_name="config")
def main(cfg) -> None:
    # set initial value
    cwd = Path(hydra.utils.get_original_cwd())
    print(f'Orig working directory : {cwd}')

    # set device
    device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
    np.random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    if device == 'cuda':
        torch.cuda.manual_seed(cfg.seed)

    # set logs dir
    num_id = len(glob.glob1(cwd / 'logs', f'{today:%Y%m%d}_*')) + 1
    log_dir = cwd / f'logs/{today:%Y%m%d}_{num_id:02}_{cfg.method}'
    writer = SummaryWriter(log_dir=log_dir)

    shutil.copytree(cwd / 'src', log_dir / 'src')

    # load dataset
    train_loader, val_loader, test_loader = get_loader(cfg, cwd)

    model = ResNetFintune(img_size=cfg.model.img_size,
                          channel=cfg.model.channel,
                          dim_feedforward=cfg.model.dim_feedforward,
                          num_classes=cfg.model.num_classes)

    dumyinput = torch.rand(cfg.batch_size, *model.input_shape)
    dumyinput = torch.arange(dumyinput.numel()).reshape(dumyinput.shape).float()
    writer.add_graph(model, dumyinput)
    summary(model, (cfg.batch_size, *model.input_shape), device='cpu')
    if torch.cuda.device_count() > 1:
        print("Let's use", torch.cuda.device_count(), "GPUs!")
        # dim = 0 [30, xxx] -> [10, ...], [10, ...], [10, ...] on 3 GPUs
        model = nn.DataParallel(model)
    model.to(device)

    optimizer = optim.Adam(model.parameters(), lr=cfg.lr)
    scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(optimizer, T_0=50, T_mult=2, eta_min=0.001)
    try:
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        n_iter = 0
        # ★EarlyStoppingクラスのインスタンス化★
        earlystopping = EarlyStopping(patience=cfg.patience, verbose=True)  # 検証なのでわざとpatience=1回にしている
        for epoch in range(cfg.epoch):
            n_iter = train_model(model, train_loader, epoch, cfg.epoch, device, optimizer, writer, n_iter)

            loss = eval_model(model, val_loader, epoch, cfg.epoch, device, writer, n_iter)
            scheduler.step(loss/cfg.batch_size)
            # ★毎エポックearlystoppingの判定をさせる★
            earlystopping(loss, model, log_dir / f'epoch{epoch:05}.pt')  # callメソッド呼び出し
            if earlystopping.early_stop:  # ストップフラグがTrueの場合、breakでforループを抜ける
                print('='*60)
                print('Early Stopping!')
                print('='*60)

                save_ep = epoch - cfg.patience
                save_model_dir = log_dir / f'epoch{save_ep:05}.pt'
                break
        print('Done.')
    except KeyboardInterrupt:
        print('='*60)
        print('Early Stopping!')
        print('='*60)
        save_ep = epoch - 1
        save_model_dir = log_dir / f'epoch{save_ep:05}.pt'
    end.record()
    torch.cuda.synchronize()
    elapsed_time = start.elapsed_time(end)
    print(elapsed_time / 1000, 'sec.')
    with open(log_dir / 'ElapsedTime.txt', 'w') as f:
        f.write(f'{elapsed_time / 1000} sec.')
    print('Make ConfusionMatrix and save the report')
    # Use for make errorimg

    train_loader.shuffle = False
    phaze = ['train', 'val', 'test']
    for idx, loader in enumerate([train_loader, val_loader, test_loader]):
        name_char_dict_path = cwd / f'../data/Googlefonts/name_char_dict_{phaze[idx]}.pt'
        ConfusionMatrix(test_loader=loader,
                        model_select=model,
                        model_PATH=save_model_dir,
                        save_dir=log_dir,
                        save_name=f'{phaze[idx]}_{save_ep:05}',
                        device_select=device,
                        name_char_dict_path=name_char_dict_path,
                        save_dir_name=log_dir / f'miss_{phaze[idx]}_{save_ep:05}',
                        nb_classes=cfg.model.num_classes,
                        cwd=cwd,
                        error_img=False)

        '''correct_img(test_loader=loader,
                    model_select=model,
                    model_PATH=save_model_dir,
                    save_dir=log_dir,
                    save_name=f'{phaze[idx]}_{save_ep:05}',
                    device_select=device,
                    name_char_dict_path=name_char_dict_path,
                    save_dir_name=log_dir / f'correct_{phaze[idx]}_{save_ep:05}')'''


if __name__ == '__main__':
    main()