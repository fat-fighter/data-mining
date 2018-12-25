import os
import models
import argparse
import base_models
import numpy as np
import tensorflow as tf
import matplotlib as mpl

from tqdm import tqdm

from matplotlib import pyplot as plt
from matplotlib import gridspec as grid

import includes.visualization as visualization
from includes.utils import load_data, generate_regression_variable

mpl.rc_file_defaults()

tf.logging.set_verbosity(tf.logging.ERROR)

parser = argparse.ArgumentParser(
    description="Training file for DMVAE and DVMOE"
)


parser.add_argument("--model", type=str, default="dmvae",
                    help="Model to use [dmvae, vade, dmoe, dvmoe, vademoe]")
parser.add_argument("--model_name", type=str, default="",
                    help="Name of the model")
parser.add_argument("--dataset", type=str, default="mnist",
                    help="Dataset to use [mnist, spiral, cifar10]")

parser.add_argument("--latent_dim", type=int, default=10,
                    help="Number of dimensions for latent variable Z")
parser.add_argument("--output_dim", type=int, default=1,
                    help="Output dimension for regression variable for ME models")
parser.add_argument("--n_classes", type=int, default=-1,
                    help="Number of clusters or classes to use for ME models")

parser.add_argument("--classification", action="store_true", default=False,
                    help="Whether the objective is classification or regression (ME models)")

parser.add_argument("--n_epochs", type=int, default=500,
                    help="Number of epochs for training the model")
parser.add_argument("--pretrain_epochs_vae", type=int, default=200,
                    help="Number of epochs for pretraining the vae model")
parser.add_argument("--pretrain_epochs_gmm", type=int, default=200,
                    help="Number of epochs for pretraining the gmm model")

parser.add_argument("--plotting", action="store_true", default=False,
                    help="Whether to generate sampling and regeneration plots")
parser.add_argument("--plot_epochs", type=int, default=100,
                    help="Nummber of epochs before generating plots")

parser.add_argument("--save_epochs", type=int, default=10,
                    help="Nummber of epochs before saving model")

args = parser.parse_args()
print(args)


def main(argv):
    dataset = argv.dataset
    latent_dim = argv.latent_dim
    output_dim = argv.output_dim

    n_classes = argv.n_classes

    model_str = argv.model
    model_name = argv.model_name

    plotting = argv.plotting
    plot_epochs = argv.plot_epochs

    save_epochs = argv.save_epochs

    classification = argv.classification

    moe = model_str[-3:] == "moe"

    n_epochs = argv.n_epochs
    pretrain_epochs_vae = argv.pretrain_epochs_vae
    pretrain_epochs_gmm = argv.pretrain_epochs_gmm

    dataset = load_data(
        dataset, classification=classification, output_dim=output_dim
    )

    if model_name == "":
        model_name = model_str

    n_classes = dataset.n_classes
    if argv.n_classes > 0:
        n_classes = argv.n_classes

    if moe:
        n_experts = n_classes
        if classification:
            output_dim = dataset.n_classes

        from includes.utils import MEDataset as Dataset

        if model_str not in ["dmoe", "vademoe", "dvmoe"]:
            raise NotImplementedError

        if model_str == "dmoe":
            model = models.DeepMoE(
                model_str, dataset.input_dim, output_dim, n_experts, classification,
                activation=tf.nn.relu, initializer=tf.contrib.layers.xavier_initializer
            )
            model.build_graph(
                [512, 256]
            )

            plotting = False

        elif model_str == "dvmoe":
            model = models.DeepVariationalMoE(
                model_str, dataset.input_type, dataset.input_dim, latent_dim, output_dim, n_experts,
                classification, activation=tf.nn.relu, initializer=tf.contrib.layers.xavier_initializer
            ).build_graph()

        elif model_str == "vademoe":
            model = models.VaDEMoE(
                model_str, dataset.input_type, dataset.input_dim, latent_dim, output_dim, n_experts,
                classification, activation=tf.nn.relu, initializer=tf.contrib.layers.xavier_initializer
            ).build_graph()

        test_data = (
            dataset.test_data, dataset.test_classes, dataset.test_labels
        )
        train_data = (
            dataset.train_data, dataset.train_classes, dataset.train_labels
        )

    else:
        n_clusters = n_classes

        from includes.utils import Dataset

        if model_str not in ["dmvae", "vade"]:
            raise NotImplementedError

        if model_str == "dmvae":
            model = base_models.DeepMixtureVAE(
                model_name, dataset.input_type, dataset.input_dim, latent_dim, n_clusters,
                activation=tf.nn.relu, initializer=tf.contrib.layers.xavier_initializer
            ).build_graph()
        elif model_str == "vade":
            model = base_models.VaDE(
                model_name, dataset.input_type, dataset.input_dim, latent_dim, n_clusters,
                activation=tf.nn.relu, initializer=tf.contrib.layers.xavier_initializer
            ).build_graph(
                {"Z": [512, 256, 256]}, [256, 256, 512]
            )

        dataset.train_data = np.concatenate(
            [dataset.train_data, dataset.test_data], axis=0
        )
        dataset.train_classes = np.concatenate(
            [dataset.train_classes, dataset.test_classes], axis=0
        )

        test_data = (dataset.test_data, dataset.test_classes)
        train_data = (dataset.train_data, dataset.train_classes)

    test_data = Dataset(test_data, batch_size=100)
    train_data = Dataset(train_data, batch_size=100)

    model.define_train_step(0.002, train_data.epoch_len * 25)

    if model_str in ["vade", "dvmoe", "vademoe"]:
        model.define_pretrain_step(0.002, train_data.epoch_len * 10)

    model.path = "saved_models/%s/%s" % (dataset.datagroup, model.name)
    for path in [model.path + "/" + x for x in ["model", "vae", "prior"]]:
        if not os.path.exists(path):
            os.makedirs(path)

    sess = tf.Session()
    tf.global_variables_initializer().run(session=sess)

    if model_str in ["dvmoe", "vademoe"]:
        model.pretrain(
            sess, train_data, pretrain_epochs_vae
        )
    elif model_str in ["vade"]:
        model.pretrain(
            sess, train_data, pretrain_epochs_vae, pretrain_epochs_gmm
        )

    var_list = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)
    saver = tf.train.Saver(var_list)
    ckpt_path = model.path + "/model/parameters.ckpt"

    try:
        saver.restore(sess, ckpt_path)
    except:
        print("Could not load trained model")

    with tqdm(range(n_epochs), postfix={"loss": "inf", "accy": "0.00%"}) as bar:
        accuracy = 0.0
        max_accuracy = 0.0

        for epoch in bar:
            if plotting and epoch % plot_epochs == 0:
                if dataset.sample_plot is not None:
                    dataset.sample_plot(model, sess)
                if dataset.regeneration_plot is not None:
                    dataset.regeneration_plot(model, test_data, sess)

            if epoch % save_epochs == 0:
                accuracy = model.get_accuracy(sess, train_data)
                if accuracy > max_accuracy:
                    max_accuracy = accuracy
                    saver.save(sess, ckpt_path)

            if moe:
                bar.set_postfix({
                    "loss": "%.4f" % model.train_op(sess, train_data),
                    "lsqe": "%.4f" % model.get_accuracy(sess, test_data)
                })
            else:
                bar.set_postfix({
                    "loss": "%.4f" % model.train_op(sess, train_data),
                    "accy": "%.2f%%" % accuracy
                })

    if plotting:
        dataset.sample_plot(model, sess)
        dataset.regeneration_plot(model, test_data, sess)


if __name__ == "__main__":
    main(args)
