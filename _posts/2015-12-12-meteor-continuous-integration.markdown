---
layout: post
title:  "Meteor Continuous Integration & Deployment - Bitbucket to Digital Ocean"
date:   2015-12-11 13:33:04
categories: meteor
summary: Source code for the meteor application is in a private repository in bitbucket. It is deployed as a dockerised node application in Digital Ocean. Master branch is used for staging and the release branch is used for production. Whenever there is a change in the master branch, velocity tests are to run if it all works, it is to be deployed as a dockerised container in digital ocean.

---
# **Meteor Continuous Integration & Deployment - Bitbucket to Digital Ocean.**

## Problem Statement

Source code for the meteor application is in a private repository in bitbucket. It is deployed as a dockerised node application in Digital Ocean. Master branch is used for staging and the release branch is used for production. Whenever there is a change in the master branch, velocity tests are to run if it all works, it is to be deployed.

## Solution

The solution described below uses a free bitbucket account, free wercker account (allows two concurrent builds) and an account in tutum(currently it is free Beta, not sure how it will be in the future when it becomes GA.).

### Build
Continuous Integration(CI) is an easy problem to solve with various given examples if the repository is github. I tried shippable and semaphore first, with no success towards the end goal. Shippable was very close, however, it was very slow and found it difficult to customise.

Finally the one which worked like a breeze is [Wercker](http://wercker.com/).

Wercker allows you to define your own custom steps in their build and deploy steps. (It was great if they supported the facility to define your own pipelines too.)

For example, define a wercker.yml file in your root repository for meteor's velocity testing. (refer to [this example project](https://github.com/lucidprogrammer/meteor-watson))
Note that you can define your own custom base docker image for your build step, this is a great value add.

{% highlight java %}
build:
  box: lucidprogrammer/meteor-velocity-base
  steps:
    - script:
        name: Run the test suites
        code: velocity test-packages ./ --ci
{% endhighlight %}
### Deploy

As wercker allows you to define a different base image for deploy too, it becomes very helpful. Plus it allows you to push the resulting image to your own defined private registry.

I am using [tutum](http://tutum.co) for orchestration.

An example, wercker deploy pipeline,

{% highlight java %}
deploy:
  box: lucidprogrammer/meteor-production-base
  steps:
    - script:
        name: bundle meteor source
        code: meteor build --directory /meteor
    - script:
        name: npm install
        code: cd /meteor/bundle/programs/server/ && npm install && touch /meteor/bundle/.foreverignore
    - script:
        # sysctl to fix Waiting...Fatal error: watch ENOSPC Ref: http://stackoverflow.com/questions/16748737/grunt-watch-error-waiting-fatal-error-watch-enospc
        name: sysctl configuration
        code: echo fs.inotify.max_user_watches=524288 | tee -a /etc/sysctl.conf
    - script:
        name: sysctl
        code: sysctl -p
    - internal/docker-push:
        #provide tutum username and password as env settings in your deploy target in wercker.
        username: $TUTUMUSER
        password: $TUTUMPASSWORD
        repository: tutum.co/yourname/yourrepositoryname
        registry: tutum.co

{% endhighlight %}

#### Things to do at Tutum

First thing to do is to setup a node. (you can use bring your own node or create a Digital Ocean node directly from tutum.) Tag your node, example "staging".
Similar to docker-compose, you can create a tutum.yml file which defines your services.

An example as follows,

{% highlight java %}
mongo:
  image: tutum/mongodb:3.0
  restart: on-failure
  target_num_containers: 1
  tags:
    - staging
  volumes:
    - /root/production/mongo:/data/db
  environment:
    - MONGODB_PASS=password
  deployment_strategy: high_availability
  sequential_deployment: true

web:
  image: tutum.co/yourname/yourrepositoryname:latest
  # Refer to https://github.com/docker/docker/issues/4611
  # Using sysctl in the locum production CMD
  privileged: true
  # when the image is updated, redeploy the container
  autoredeploy: true
  restart: on-failure
  deployment_strategy: high_availability
  sequential_deployment: true
  target_num_containers: 1
  # which of the node/s are we to deploy
  tags:
    - staging
  ports:
    - "80:80"
  links:
    - mongo
  environment:
    - ROOT_URL=http://yourapp.com
    - MONGO_URL=mongodb://admin:password@mongo:27017
    - PORT=80
  command: /usr/local/bin/forever ./main.js
  working_dir: /meteor/bundle
  ports:
    - "80:80"


{% endhighlight %}
