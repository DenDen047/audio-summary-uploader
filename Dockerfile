FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04

# environment variables
ENV PIPENV_PYTHON_VERSION=3.11
ENV USER_NAME=user
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Tokyo
# CUDA path
ENV CUDA_HOME=/usr/local/cuda
ENV CUDA_ROOT=/usr/local/cuda
ENV PATH=$PATH:$CUDA_ROOT/bin
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$CUDA_ROOT/lib64:$CUDA_ROOT/lib:/usr/local/nvidia/lib64:/usr/local/nvidia/lib:/usr/lib/x86_64-linux-gnu:/usr/local/cuda/lib64:/usr/local/cuda/lib
ENV LIBRARY_PATH=$LIBRARY_PATH:$LD_LIBRARY_PATH:/usr/local/cuda/lib64/stubs
ENV FORCE_CUDA=1
ENV TORCH_CUDA_ARCH_LIST="8.6;9.0;12.0"
ENV TORCH_VERSION=2.8.0
ENV CUDA_VERSION=cu128

# basic libs
RUN apt update \
    && apt install -y software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt update \
    && apt install -y \
        vim sudo zip unzip wget git curl g++ \
        build-essential \
        git-flow \
        gfortran pkg-config ffmpeg exiftool locales-all libopencv-dev \
        python${PIPENV_PYTHON_VERSION} \
        python${PIPENV_PYTHON_VERSION}-dev \
        python${PIPENV_PYTHON_VERSION}-distutils \
    && rm /usr/bin/python3 \
    && ln -s /usr/bin/python${PIPENV_PYTHON_VERSION} /usr/bin/python3 \
    && ln -s /usr/bin/python3 /usr/bin/python \
    && curl -sS https://bootstrap.pypa.io/get-pip.py | python3 \
    && adduser --disabled-password --gecos '' $USER_NAME \
    && adduser $USER_NAME sudo \
    && echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
COPY ./entrypoint.sh /home/$USER_NAME

# pip libs
RUN python3 -m pip install -U pip
RUN python3 -m pip install \
    scikit-image matplotlib imageio opencv-python \
    plotly \
    pandas \
    tqdm \
    loguru \
    tensorboard \
    ipython ipykernel

# PyTorch
RUN python3 -m pip install torch==${TORCH_VERSION}+${CUDA_VERSION} torchvision --index-url https://download.pytorch.org/whl/${CUDA_VERSION}


# # Clean
# RUN apt -y autoremove -y --purge \
#   && apt -y clean \
#   && apt -y autoclean \
#     && rm -rf /var/lib/apt/lists/*

USER $USER_NAME
WORKDIR /home/$USER_NAME/
