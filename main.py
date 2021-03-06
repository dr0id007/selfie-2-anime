from UGATIT import UGATIT
import argparse
from flask import Flask, request, Response, render_template, redirect, session, url_for
from flask_uploads import UploadSet, configure_uploads, IMAGES, patch_request_class
from flask_dropzone import Dropzone
import os
from utils import *
from email_service import *
import cv2
import io
import numpy
import json
import jsonpickle
import uuid
import time


app = Flask(__name__)
dropzone = Dropzone(app)


app.config['SECRET_KEY'] = 'supersecretkeygoeshere'

# Dropzone settings
app.config['DROPZONE_UPLOAD_MULTIPLE'] = False
app.config['DROPZONE_ALLOWED_FILE_CUSTOM'] = True
app.config['DROPZONE_ALLOWED_FILE_TYPE'] = 'image/*'
app.config['DROPZONE_REDIRECT_VIEW'] = 'results'

# Uploads settings
app.config['UPLOADED_PHOTOS_DEST'] = os.getcwd() + '/uploads'

photos = UploadSet('photos', IMAGES)
configure_uploads(app, photos)
patch_request_class(app)  # set maximum file size, default is 16MB

"""parsing and configuration"""


def parse_args():
    desc = "Tensorflow implementation of U-GAT-IT"
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument('--phase', type=str, default='web',
                        help='[train / test / web / runner]')
    parser.add_argument('--light', type=str2bool, default=True,
                        help='[U-GAT-IT full version / U-GAT-IT light version]')
    parser.add_argument('--dataset', type=str,
                        default='selfie2anime', help='dataset_name')

    parser.add_argument('--epoch', type=int, default=100,
                        help='The number of epochs to run')
    parser.add_argument('--iteration', type=int, default=10000,
                        help='The number of training iterations')
    parser.add_argument('--batch_size', type=int, default=1,
                        help='The size of batch size')
    parser.add_argument('--print_freq', type=int, default=1000,
                        help='The number of image_print_freq')
    parser.add_argument('--save_freq', type=int, default=1000,
                        help='The number of ckpt_save_freq')
    parser.add_argument('--decay_flag', type=str2bool,
                        default=True, help='The decay_flag')
    parser.add_argument('--decay_epoch', type=int,
                        default=50, help='decay epoch')

    parser.add_argument('--lr', type=float, default=0.0001,
                        help='The learning rate')
    parser.add_argument('--GP_ld', type=int, default=10,
                        help='The gradient penalty lambda')
    parser.add_argument('--adv_weight', type=int,
                        default=1, help='Weight about GAN')
    parser.add_argument('--cycle_weight', type=int,
                        default=10, help='Weight about Cycle')
    parser.add_argument('--identity_weight', type=int,
                        default=10, help='Weight about Identity')
    parser.add_argument('--cam_weight', type=int,
                        default=1000, help='Weight about CAM')
    parser.add_argument('--gan_type', type=str, default='lsgan',
                        help='[gan / lsgan / wgan-gp / wgan-lp / dragan / hinge]')

    parser.add_argument('--smoothing', type=str2bool,
                        default=True, help='AdaLIN smoothing effect')

    parser.add_argument('--ch', type=int, default=64,
                        help='base channel number per layer')
    parser.add_argument('--n_res', type=int, default=4,
                        help='The number of resblock')
    parser.add_argument('--n_dis', type=int, default=6,
                        help='The number of discriminator layer')
    parser.add_argument('--n_critic', type=int, default=1,
                        help='The number of critic')
    parser.add_argument('--sn', type=str2bool, default=True,
                        help='using spectral norm')

    parser.add_argument('--img_size', type=int,
                        default=256, help='The size of image')
    parser.add_argument('--img_ch', type=int, default=3,
                        help='The size of image channel')
    parser.add_argument('--augment_flag', type=str2bool,
                        default=True, help='Image augmentation use or not')

    parser.add_argument('--checkpoint_dir', type=str, default='checkpoint',
                        help='Directory name to save the checkpoints')
    parser.add_argument('--result_dir', type=str, default='results',
                        help='Directory name to save the generated images')
    parser.add_argument('--log_dir', type=str, default='logs',
                        help='Directory name to save training logs')
    parser.add_argument('--sample_dir', type=str, default='samples',
                        help='Directory name to save the samples on training')

    return check_args(parser.parse_args())


"""checking arguments"""


def check_args(args):
    # --checkpoint_dir
    check_folder(args.checkpoint_dir)

    # --result_dir
    check_folder(args.result_dir)

    # --result_dir
    check_folder(args.log_dir)

    # --sample_dir
    check_folder(args.sample_dir)

    # --epoch
    try:
        assert args.epoch >= 1
    except:
        print('number of epochs must be larger than or equal to one')

    # --batch_size
    try:
        assert args.batch_size >= 1
    except:
        print('batch size must be larger than or equal to one')
    return args


def runner(args):
    # open session
    with tf.Session(config=tf.ConfigProto(allow_soft_placement=True)) as sess:
        gan = UGATIT(sess, args)

        # build graph
        gan.build_model()

        # show network architecture
        show_all_variables()

        # SQS client for DLQ
        sqs = boto3.client('sqs')
        dlq_queue = sqs.get_queue_url(QueueName=os.environ['DLQ_NAME'])

        # email service
        email = EmailService()

        gan.test_endpoint_init()
        while True:
            time.sleep(1)
            messages = get_messages_from_queue()
            total_msg = len(messages)
            if total_msg > 0:
                print("[INFO] Retrieved " + str(total_msg) + " messages")
            for message in messages:
                print("[INFO] Message " + str(total_msg) + " being processed")
                body = json.loads(message['Body'])
                try:
                    try:
                        # By default crop image (cropping to occur in lambda soon)
                        if 'bucket_cropped_key' in body:
                            crop = False
                        else:
                            crop = True

                        bucket = body['bucket_name']
                        if crop:
                            bucket_key = body['bucket_key']
                        else:
                            bucket_key = body['bucket_cropped_key']

                        file_name = body['file_name']
                        email_addr = body['email']
                        token = body['token']

                        if crop:
                            crop = body['crop']
                            x = crop['x']
                            y = crop['y']
                            width = crop['width']
                            height = crop['height']
                    except Exception as e:
                        print()
                        print("ERROR: Parsing message")
                        print(e)
                        raise e

                    try:
                        image = download_image(bucket, bucket_key)
                    except Exception as e:
                        print("ERROR: Downloading Image")
                        print(e)
                        raise e

                    try:
                        # Change color space
                        if crop:
                            crop_img = image[y:y+height, x:x+width]
                            crop_img = cv2.cvtColor(
                                crop_img, cv2.COLOR_RGB2BGR)
                        else:
                            crop_img = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                    except Exception as e:
                        print("ERROR: Cropping Image | Changing Color space")
                        print(e)
                        raise e

                    try:
                        if crop:
                            # Resize image
                            crop_img = cv2.resize(crop_img, dsize=(256, 256))
                    except Exception as e:
                        print("ERROR: Resizing Image")
                        print(e)
                        raise e

                    try:
                        # do some fancy processing here....
                        fake_img = gan.test_endpoint(crop_img)
                    except Exception as e:
                        print("ERROR: Processing image with GAN")
                        print(e)
                        raise e

                    try:
                        # Upload to S3
                        image_url = upload_image(fake_img, file_name)
                    except Exception as e:
                        print("ERROR: Uploading image to S3")
                        print(e)
                        raise e

                    try:
                        # Send Email
                        delete_url = 'https://api.selfie2anime.com/analysis/delete?uuid={}&key={}'.format(
                            file_name, token)
                        email.send_email(email_addr, image_url, delete_url)
                    except Exception as e:
                        print("ERROR: Failed to send email")
                        print(e)
                        raise e

                except Exception as e:
                    # try:
                    #     response = sqs.send_message(QueueUrl=dlq_queue, MessageBody=body)
                    # except Exception as e:
                    #     print("ERROR: Failed to post to DLQ")
                    #     print(e)
                    print('FATAL ERROR')
                    print(e)

                total_msg = total_msg - 1
                print("[INFO] " + str(total_msg) +
                      " messages remain for worker to process")


"""main"""


def main():
    # parse arguments
    args = parse_args()
    if args is None:
        exit()

    if args.phase == 'runner':
        runner(args)

    if args.phase == 'web':
        app.run(host="0.0.0.0", port=5000, debug=True)

    # open session
    with tf.Session(config=tf.ConfigProto(allow_soft_placement=True)) as sess:
        gan = UGATIT(sess, args)

        # build graph
        gan.build_model()

        # show network architecture
        show_all_variables()

        if args.phase == 'train':
            gan.train()
            print(" [*] Training finished!")

        if args.phase == 'test':
            gan.test()
            print(" [*] Test finished!")

# route http posts to this method


@app.route('/', methods=['GET', 'POST'])
def index():
    # set session for image results
    if "file_urls" not in session:
        session['file_urls'] = []
    # list to hold our uploaded image urls
    file_urls = session['file_urls']

    try:
        if request.method == 'POST':
            file_obj = request.files
            for f in file_obj:
                file = request.files.get(f)

                # convert string of image data to uint8
                nparr = np.fromfile(file, np.uint8)
                # decode image
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                # parse arguments
                args = parse_args()
                if args is None:
                    exit()

                # open session
                with tf.Session(config=tf.ConfigProto(allow_soft_placement=True)) as sess:
                    gan = UGATIT(sess, args)

                    # build graph
                    gan.build_model()

                    # show network architecture
                    show_all_variables()

                    # do some fancy processing here....
                    # fake_img = gan.test_endpoint(img)

                    try:
                        # do some fancy processing here....
                        gan.test_endpoint_init()
                        fake_img = gan.test_endpoint(img)
                    except Exception as e:
                        print("ERROR: Processing image with GAN")
                        print(e)
                        raise e

                    # save the file with to our photos folder
                    filename = str(uuid.uuid1()) + '.png'
                    cv2.imwrite('uploads/' + filename, fake_img)
                    # append image urls
                    file_urls.append(photos.url(filename))

            session['file_urls'] = file_urls
            return "uploading..."
    except Exception as ex:
        print(ex)
    return render_template('index.html')


@app.route('/api', methods=['GET', 'POST'])
def api():
    try:
        if request.method == 'POST':
            file_obj = request.files
            for f in file_obj:
                file = request.files.get(f)

                # convert string of image data to uint8
                nparr = np.fromfile(file, np.uint8)
                # decode image
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                # parse arguments
                args = parse_args()
                if args is None:
                    exit()

                # open session
                with tf.Session(config=tf.ConfigProto(allow_soft_placement=True)) as sess:
                    gan = UGATIT(sess, args)

                    # build graph
                    gan.build_model()

                    # show network architecture
                    show_all_variables()

                    # do some fancy processing here....
                    # fake_img = gan.test_endpoint(img)

                    try:
                        # do some fancy processing here....
                        gan.test_endpoint_init()
                        fake_img = gan.test_endpoint(img)
                    except Exception as e:
                        print("ERROR: Processing image with GAN")
                        print(e)
                        raise e

                    # save the file with to our photos folder
                    filename = str(uuid.uuid1()) + '.png'
                    cv2.imwrite('uploads/' + filename, fake_img)
                    # append image urls
                    file_urls.append(photos.url(filename))

            session['file_urls'] = file_urls
            return "uploading..."
    except Exception as ex:
        print(ex)


@app.route('/results')
def results():

    # redirect to home if no images to display
    if "file_urls" not in session or session['file_urls'] == []:
        return redirect(url_for('index'))

    # set the file_urls and remove the session variable
    file_urls = session['file_urls']
    session.pop('file_urls', None)

    return render_template('results.html', file_urls=file_urls)


if __name__ == '__main__':
    main()
