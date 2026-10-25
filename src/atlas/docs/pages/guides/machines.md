---
title: "Several machines"
description: "Install Atlas on a home lab, a VPS or the office PC and open it from the app on your laptop, over Tailscale or an SSH tunnel."
sidebar:
  order: 2
---

Install Atlas on any machine you have, a home lab, a company VPS or the office PC, and open it from the app on your laptop. The Atlas over there does the work, with that machine's files, keys and approvals. The app is only the window.

## Over Tailscale

Tailscale needs no open port and no certificate.

1. On the other machine, open the app's **Settings**, then **Machines**, and choose **Open to my machines**. It shows a connection code. Without the app there, `atlas server code` prints it in a terminal.
2. On your laptop, choose **Add a machine** in **Settings**, then **Machines**, pick **Tailscale** and paste the code.

The code holds that home's password, so paste it only into your own app. **Change the password** makes every code given before stop working.

## Over SSH

If you reach the machine with SSH already, Atlas uses the keys you have. Choose **Add a machine**, pick **SSH tunnel**, and give the host as `user@host` or a name from your `~/.ssh/config`.

## Always on

A home can run as a service: turn on **Always on** and it runs with the app closed and starts with the computer. Routines then run on time, and every window you open sees the same conversations.

## A terminal on each machine

The side panel holds a terminal on the machine the Atlas you are looking at runs on, wherever the app is. Previews of files, local pages and what the agent builds come from that machine too.

## Separate homes

On one machine you can keep several homes, for example one for work and one for personal life. Each has its own memory, keys and conversations, and the app shows which one you are in.
