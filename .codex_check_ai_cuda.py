import torch, sys, os
print('torch_file=', torch.__file__)
print('torch_version=', torch.__version__)
print('torch_cuda_version=', getattr(torch.version, 'cuda', None))
print('cuda_available=', torch.cuda.is_available())
print('device_count=', torch.cuda.device_count())
if torch.cuda.is_available():
    print('device0=', torch.cuda.get_device_name(0))
print('sys_path_head=')
for p in sys.path[:8]: print(p)
