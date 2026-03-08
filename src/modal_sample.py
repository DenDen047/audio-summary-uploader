import modal

dockerfile_image = modal.Image.from_dockerfile(
    "docker/Dockerfile",
    build_args={
        "BASE_IMAGE": "nvidia/cuda:12.1.0-devel-ubuntu20.04",
        "PYTHON_VERSION": "3.11",
        "FORCE_CUDA": "1",
        "UID": "1000",
        "GID": "1000",
        "USER_NAME": "ubuntu",
        "GROUP_NAME": "ubuntu",
        "TORCH_CUDA_ARCH_LIST": "7.5,8.6",
    },
)
app = modal.App(name="sample-app", image=dockerfile_image)


@app.function(image=dockerfile_image, gpu="T4")
def app_function():
    import subprocess

    import torch

    subprocess.run(["nvidia-smi"], shell=True)
    cuda_available = torch.cuda.is_available()
    print(f"CUDA Available: {cuda_available}")
    if cuda_available:
        current_device_index = torch.cuda.current_device()
        device_name = torch.cuda.get_device_name(current_device_index)
        print(f"Current Device Index: {current_device_index}")
        print(f"Current Device Name: {device_name}")


@app.local_entrypoint()
def main():
    app_function.remote()
