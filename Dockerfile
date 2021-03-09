FROM tensorflow/tensorflow:1.14.0-gpu-py3

COPY ./checkpoint/ /app/checkpoint

RUN pip3 install \
    boto3==1.12.46 \
    Flask-Dropzone==1.5.4 \
    flask-uploads==0.2.1 \
    requests==2.23.0 \
    jsonpickle==1.4.1 \
    Flask==1.1.2 \
    Pillow==7.1.2 \
    opencv-python==4.2.0.34 \
    matplotlib==3.0.0 \
    numpy==1.15.2
RUN apt-get update && apt-get install -y \
    curl \
    lsb-release \
    sudo \
    rsync \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY ./templates/ /app/templates
COPY *.py /app/

WORKDIR /app

ENTRYPOINT [ "python3" ]
CMD [ "main.py" ]