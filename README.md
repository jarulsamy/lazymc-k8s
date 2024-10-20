# lazymc-k8s

A wrapper over [lazymc](https://github.com/timvisee/lazymc) for Kubernetes.

> Warning: this is just a PoC right now. It does work reasonably well in my
> testing so far though. Contributions and feedback are welcome!

# Motivation

The upstream [lazymc](https://github.com/timvisee/lazymc) project allows
automated starting/stopping of a Minecraft server depending on player needs.
This projects aims to integrate this with Kubernetes to dynamically scale a
deployment up/down instead. This grants some extreme flexibility with
dynamically spinning up Minecraft servers as needed.

# How it Works

<img src="./docs/diagram.drawio.svg" alt="K8s digram" width="200"/>

- lazymc spawns a custom Python script which automatically scales up a server
  deployment.
- When lazymc terminates the script, a signal handler automatically scales down
  the server instance.
- Players can connect using a standard service (either a LoadBalancer or a
  cluster specific service depending on you CNI).
- Initial version is very simple, more of a PoC.

# Usage

There are a few requirements to use this:

- The server instance and proxy must run in the same namespace (this is an
  arbitrary restriction right now, and can be tweaked in the code if need be).

- A cluster role and cluster role binding must be created for the default
  service account of the namespace. Currently, lazymc-k8s does not support
  using an alternate service account.

I generally recommend running your Minecraft server in it's own namespace
anyway.

File copies of all these examples are in [example](./example).

Create a dedicated namespace.

```cli
$ kubectl create ns minecraft
```

Create the required ClusterRole and ClusterRoleBinding with these definitions.
These grant the service account some permissions to modify K8s deployments via
the API.

```yaml
# ClusterRole.yml
---
kind: ClusterRole
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: lazymc
rules:
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments/scale"]
    verbs: ["get", "list", "watch", "update", "patch"]
```

```yaml
# ClusterRoleBinding.yml
---
kind: ClusterRoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: lazymc-binding
subjects:
  - kind: ServiceAccount
    name: default
    namespace: minecraft
roleRef:
  kind: ClusterRole
  name: lazymc
  apiGroup: rbac.authorization.k8s.io
```

```cli
$ kubectl create -f ClusterRole.yml -f ClusterRoleBinding.yml
```

Now, create two services, one for external access to the proxy, and another for
the server pod.

```yaml
# svc.yml
---
kind: Service
apiVersion: v1
metadata:
  name: minecraft
namespace: minecraft
spec:
type: ClusterIP
  selector:
    app: minecraft
ports:
    - name: mc-tcp
      port: 25566
      targetPort: 25566
      protocol: TCP
    - name: mc-udp
      port: 25566
      targetPort: 25566
      protocol: UDP

---
# This assumes you're using a LoadBalancer for external access.
# Change this as needed.
kind: Service
apiVersion: v1
metadata:
  name: lazymc-k8s
  namespace: minecraft
spec:
  type: LoadBalancer
  selector:
    app: lazymc-k8s
  ports:
    - port: 25565
```

```cli
$ kubectl create -f svc.yml
```

Finally, we are ready to create 2 deployments, one for the lazymc proxy
and another for the actual server instance.

```yaml
# lazymc-k8s-deployment.yml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: lazymc-k8s
  namespace: minecraft
spec:
  replicas: 1
  selector:
    matchLabels:
      app: lazymc-k8s
  strategy:
    type: Recreate
  template:
    metadata:
      labels:
        app: lazymc-k8s
    spec:
      securityContext:
        fsGroup: 1000
      containers:
        - name: lazymc-k8s
          image: jarulsamy/lazymc-k8s
          env:
            - name: UID
              value: "1000"
            - name: GID
              value: "1000"
            - name: LAZYMC_K8S_DEPLOYMENT_NAME
              value: "minecraft"
          volumeMounts:
            - mountPath: /config
              name: config
      restartPolicy: Always
      volumes:
        - name: config
          hostPath:
            path: /data/lazymc
```

This uses the de-facto itzg/minecraft-server container with a PaperMC server,
though any image should work fine here. **Note:** The initial replicas must be 0.

Make sure you adjust your `server.properties` file to listen on 25566 instead of 25565.

```yaml
# server-deployment.yml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: minecraft
  namespace: minecraft
spec:
  replicas: 0
  selector:
    matchLabels:
      app: minecraft
  strategy:
    type: Recreate
  template:
    metadata:
      labels:
        app: minecraft
    spec:
      affinity:
      securityContext:
        fsGroup: 1000
      containers:
        - name: minecraft
          image: itzg/minecraft-server:latest
          env:
            - name: UID
              value: "1000"
            - name: GID
              value: "1000"
            - name: TYPE
              value: PAPER
            - name: PAPER_DOWNLOAD_URL
              value: https://api.papermc.io/v2/projects/paper/versions/1.21.1/builds/76/downloads/paper-1.21.1-76.jar
            - name: DIFFICULTY
              value: normal
            - name: EULA
              value: "TRUE"
            - name: ENABLE_RCON
              value: "TRUE"
            - name: ENABLE_ROLLING_LOGS
              value: "TRUE"
            - name: REPLACE_ENV_VARIABLES
              value: "TRUE"
            - name: USE_AIKAR_FLAGS
              value: "TRUE"
            - name: SYNC_CHUNK_WRITES
              value: "TRUE"
            - name: INIT_MEMORY
              value: 4G
            - name: MAX_MEMORY
              value: 32G
            - name: TZ
              value: America/Denver
            # Auto-Pause specific options
            - name: JVM_DD_OPTS
              value: "disable.watchdog:true"
          lifecycle:
            preStop:
              exec:
                command:
                  - "/bin/sh"
                  - "-c"
                  - "rcon-cli save-all"
          volumeMounts:
            - mountPath: /data
              name: data
      restartPolicy: Always
      volumes:
        - name: data
          hostPath:
            path: /data/mc
```

Create an initial configuration file for lazymc in the mount for the container.
The file should be mounted to `/config/lazymc.toml`. Then, you're ready to
create the deployments. A sample `lazymc.toml` is available
[here](./example/lazymc.toml).

```cli
$ kubectl create -f server-deployment.yml -f lazymc-k8s-deployment.yml
```

You should be able to connect to the server and have Kubernetes auto-spawn the
container for the server.

# Future Work

- Properly test all the functionality of lazymc with K8s. I have only tested a
  small subset of what lazymc supports so far.

- Integrate more closely with lazymc. By modifying the source of lazymc we could
  control the lifecycle of pods more carefully and elegantly.
