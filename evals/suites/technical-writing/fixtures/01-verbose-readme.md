## Getting Started With logship

Welcome to logship! We're really excited that you've decided to give logship a try. In this section of the README, we are going to walk you through everything you need to know in order to get logship up and running on your machine, so that you can start shipping your logs as quickly and easily as possible.

### Before You Begin

Before you begin the installation process, it is important to make sure that your environment meets all of the necessary requirements. First and foremost, logship requires Node.js. Specifically, you will need Node.js version 20.11 or later, because logship relies on some features that were only introduced in that version. Older versions of Node.js are not supported and logship will refuse to start on them. It is also worth mentioning that, at the present time, Windows is not supported. logship works on macOS and Linux.

### Installing logship

Installing logship is a straightforward process. To install it, you will want to use npm, which comes bundled with Node.js. Simply open up your terminal of choice and run the following command, which will install the latest 3.x release of logship globally on your system:

```sh
npm install -g logship@3
```

Once the installation has completed successfully, you should be able to run `logship --version` to verify that everything has been installed correctly.

### Configuring logship

Now that logship is installed, the next step is to configure it. logship reads its configuration from a file located at `~/.config/logship/config.toml`. You will need to create this file yourself, since logship does not create it for you automatically. The most important setting, and the only one that is actually required, is the endpoint where your logs should be sent. You can set this in the config file using the `endpoint` key, or alternatively you can set the `LOGSHIP_ENDPOINT` environment variable, which will take precedence over the config file if both are set.

There are also some optional settings that you might want to consider. By default, logship sends logs in batches of 500 lines. You can change this with the `batch_size` key. Please note that regardless of the batch size you choose, a single batch can never be larger than 10 MB; batches that would exceed this limit are split automatically.

### Trying It Out

Last but not least, before you start sending real logs, we highly recommend that you try running logship with the `--dry-run` flag. This will allow you to see exactly what logship would send, without actually sending anything to your endpoint. That way, you can make sure everything looks right.

For more details on all of the available configuration options, please see the full documentation at https://docs.logship.example/config.

We hope you enjoy using logship!
