#!/usr/bin/env python3
"""Minecraft Delpoyment Autoscaler.

Automatically scale up Deployment replicas on startup and scale down on
shutdown. Intended to be a target to lazymc within a Pod.

The target deployment must be scaled to `LAZYMC_K8S_MIN_REPLICAS` (stopped
state, probably 0) when lazymc is invoked. lazymc doesn't have a way to manage
lifetime if the server is already running before it is started.

Uses the following env vars:

  - LAZYMC_K8S_DEPLOYMENT_NAME - Name of deployment to automatically scale.
                                 (Required)
  - LAZYMC_K8S_MIN_REPLICAS    - Number of replicas to set the deployment to
                                 during the "sleeping" state (optional, def: 0)
  - LAZYMC_K8S_MAX_REPLICAS    - Number of replicas to set the deployment to
                                 during the "started" state (optional, def: 1)
  - LAZYMC_K8S_LOG_LEVEL       - Log level of this script. Any of:
                                 debug, info, warning, fatal (optional, def: info)
"""

import logging
import os
import signal
import sys
import time
from pathlib import Path
from threading import Event

import kubernetes as k8s

__version__ = "0.0.1"

def get_config():
    """Get runtime config from env vars."""
    # Get my namespace by reading a special file
    # Assume the server pod will run in the same namespace as the proxy.
    namespace_file_path = Path(
        "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
    )
    try:
        namespace = namespace_file_path.read_text()
    except FileNotFoundError as e:
        raise FileNotFoundError(
            "Namespace magic file missing, likely not in a kubernetes context."
        ) from e

    min_replica_count = os.getenv("LAZYMC_K8S_MIN_REPLICAS", 0)
    max_replica_count = os.getenv("LAZYMC_K8S_MAX_REPLICAS", 1)

    depl_name = os.getenv("LAZYMC_K8S_DEPLOYMENT_NAME")
    if depl_name is None:
        raise KeyError(
            "Missing required environment variable 'LAZYMC_K8S_DEPLOYMENT_NAME'"
        )

    log_level_str = os.getenv("LAZYMC_K8S_LOG_LEVEL", "info").lower().strip()
    log_levels = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.error,
        "fatal": logging.fatal,
    }

    return {
        "depl_namespace": namespace,
        "depl_name": depl_name,
        "min_replica_count": min_replica_count,
        "max_replica_count": max_replica_count,
        "log_level": log_levels.get(log_level_str, logging.INFO),
    }


def main():
    """Main Entrypoint."""
    log_format = " %(levelname).4s  k8s-handler > %(message)s"
    logging.basicConfig(format=log_format, level=logging.DEBUG)

    # Setup the k8s config from within the pod.
    k8s.config.load_incluster_config()
    apps_api = k8s.client.AppsV1Api()

    config = get_config()
    depl_name = config["depl_name"]
    depl_namespace = config["depl_namespace"]

    min_replica_count = config["min_replica_count"]
    max_replica_count = config["max_replica_count"]

    logging.debug("Loaded cluster config")

    def sigterm_handler(sig, frame):
        # Handle a SIGTERM
        # Scale the replicas to 0 and quit.
        logging.info("Scaling deployment %s to %d", depl_name, min_replica_count)
        depl_scale = apps_api.read_namespaced_deployment_scale(
            depl_name, depl_namespace
        )
        depl_scale.spec.replicas = min_replica_count
        apps_api.patch_namespaced_deployment_scale(
            depl_name, depl_namespace, depl_scale
        )
        # Wait to make sure the server stops
        wait_time = 0
        timeout = 120
        while wait_time < timeout:
            depl_scale = apps_api.read_namespaced_deployment_scale(
                depl_name, depl_namespace
            )
            if depl_scale.status.replicas == min_replica_count:
                logging.info(
                    "Successfully scaled deployment %s to %d",
                    depl_name,
                    min_replica_count,
                )
                break
            wait_time += 5
            time.sleep(5)

        else:
            logging.error("Failed to scale down deployment")
            logging.fatal("Bailing out, you're on your own.")
            sys.exit(1)

        # Finally, die gracefully
        sys.exit(0)

    # Register the interrupt handler
    signal.signal(signal.SIGTERM, sigterm_handler)
    logging.debug("Registered interrupt handler.")

    depl_scale = apps_api.read_namespaced_deployment_scale(depl_name, depl_namespace)
    num_replicas = depl_scale.spec.replicas

    # Scale up (started state)
    if num_replicas != max_replica_count:
        logging.info("Scaling deployment %s to %d", depl_name, max_replica_count)
        depl_scale.spec.replicas = max_replica_count
        apps_api.patch_namespaced_deployment_scale(
            depl_name, depl_namespace, depl_scale
        )

    # Wait indefinitely
    logging.debug("Waiting indefinitely")
    Event().wait()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Dump errors to stdout as well.
        print(e)
        raise e
