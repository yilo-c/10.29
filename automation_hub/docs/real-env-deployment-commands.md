<!-- 模块说明：真实环境部署命令清单。 -->

# 真实环境部署命令清单

本文档提供一套基于当前仓库实际 Helm chart 的真实环境部署命令顺序。

适用场景：

- `staging`
- `pre-prod`
- 生产前灰度环境
- 正式生产环境

不适用场景：

- Docker Desktop 本地集群
- 本机三容器自部署验证
- 不具备 Kubernetes + Helm 条件的临时环境

## 一、先决定部署形态

当前仓库真实环境推荐只有两种：

1. 通用真实环境：使用 [../deploy/charts/automation-hub/values-prod-external-redis.yaml](../deploy/charts/automation-hub/values-prod-external-redis.yaml)
2. Docker executor 真实环境：使用 [../deploy/charts/automation-hub/values-prod-docker-executor.yaml](../deploy/charts/automation-hub/values-prod-docker-executor.yaml)

选择规则：

- 如果 worker 节点不能确认支持 `docker.sock` hostPath，就不要选 Docker executor 模板。
- 如果集群是 containerd-only，不要直接使用 Docker executor 模板。
- 只要 `DATABASE_PATH` 仍指向 SQLite 文件，API 就保持单副本。

## 二、部署前要准备的变量

下面这些变量建议先在终端里统一设置：

```powershell
$Namespace = "automation-hub-prod"
$Release = "automation-hub-prod"
$ImageRepository = "<your-registry>/automation-hub"
$ImageTag = "<your-tag>"
$SecretName = "automation-hub-prod-secret"
$RedisUrl = "redis://<redis-host>:6379/0"
$ValuesFile = "automation-hub/deploy/charts/automation-hub/values-prod-external-redis.yaml"
$RuntimeProfile = "external-redis"
```

如果目标环境要启用 Docker executor，把最后两项改成：

```powershell
$ValuesFile = "automation-hub/deploy/charts/automation-hub/values-prod-docker-executor.yaml"
$RuntimeProfile = "docker-executor"
```

## 二点一、按当前选择收敛出的 automation-hub-test 环境建议值

如果按当前这次对话里给出的选择：

- 命名空间：`automation-hub-test`
- 部署形态：普通真实环境
- Redis / Secret：优先使用仓库内已有生产模板默认值

则建议先用下面这组变量启动：

```powershell
$Namespace = "automation-hub-test"
$Release = "automation-hub-test"
$ImageRepository = "ghcr.io/<your-account>/automation-hub"
$ImageTag = "test"
$SecretName = "automation-hub-prod-secret"
$RedisUrl = "redis://redis.example.svc.cluster.local:6379/0"
$ValuesFile = "automation-hub/deploy/charts/automation-hub/values-prod-external-redis.yaml"
$RuntimeProfile = "external-redis"
```

说明：

- `$SecretName` 是从本地生产模板和预检脚本中检索到的既有默认名。
- `$RedisUrl` 是从 [../deploy/charts/automation-hub/values-prod-external-redis.yaml](../deploy/charts/automation-hub/values-prod-external-redis.yaml) 检索到的默认外部 Redis 占位地址。
- `$ImageRepository` 不能直接从本地检索出真实可用值，因为当前仓库没有已发布的远端应用镜像地址；如果你要用公网/云上更合适的镜像托管目标，当前最推荐的是 GitHub Container Registry，也就是 `ghcr.io/<your-account>/automation-hub`。
- 在真正执行前，你仍然需要把 `<your-account>` 替换成你自己的 GHCR 账户或组织名。

## 二点二、哪些镜像可以直接通过 Docker Desktop 拉取

当前仓库相关镜像分成两类：

### 可以直接拉取的公共依赖镜像

- `python:3.11-slim`
- `redis:7-alpine`
- `curlimages/curl:8.6.0`

这些镜像都来自公开仓库，通常可以直接通过 Docker Desktop 拉取：

```powershell
docker pull python:3.11-slim
docker pull redis:7-alpine
docker pull curlimages/curl:8.6.0
```

说明：

- `python:3.11-slim` 是 [../Dockerfile](../Dockerfile) 的基础镜像。
- `redis:7-alpine` 是 chart 默认内置 Redis 镜像，定义见 [../deploy/charts/automation-hub/values.yaml](../deploy/charts/automation-hub/values.yaml)。
- `curlimages/curl:8.6.0` 是 chart 中 cronjob 的基础镜像，定义见 [../deploy/charts/automation-hub/values.yaml](../deploy/charts/automation-hub/values.yaml)。

### 不能直接拉取成品的应用镜像

- `automation-hub` 应用镜像本身

原因很直接：当前仓库里没有已配置好的公开成品镜像地址，chart 里的 `automation-hub:stable` 只是占位，不是可直接依赖的发布源。

所以应用镜像仍然需要你自己构建并推送到一个集群可拉取的仓库，例如 GHCR：

```powershell
docker build --pull -t ghcr.io/<your-account>/automation-hub:test -f automation-hub/Dockerfile automation-hub
docker push ghcr.io/<your-account>/automation-hub:test
```

如果你不打算使用 GHCR，也可以把 `$ImageRepository` 改成你现有 Harbor、Docker Hub 私有仓库或云厂商镜像仓库地址，只要目标集群可拉取即可。

## 二点三、automation-hub-test 实际执行顺序

如果你现在就要开始部署 `automation-hub-test`，推荐按下面顺序执行。

### 1. 先拉公共依赖镜像

```powershell
docker pull python:3.11-slim
docker pull redis:7-alpine
docker pull curlimages/curl:8.6.0
```

说明：这些镜像用于本地构建或 chart 依赖校验，能提前验证 Docker Desktop 网络是否正常。

### 2. 设置 test 环境变量

```powershell
$Namespace = "automation-hub-test"
$Release = "automation-hub-test"
$ImageRepository = "ghcr.io/<your-account>/automation-hub"
$ImageTag = "test"
$SecretName = "automation-hub-prod-secret"
$RedisUrl = "redis://<your-real-redis-host>:6379/0"
$ValuesFile = "automation-hub/deploy/charts/automation-hub/values-prod-external-redis.yaml"
$RuntimeProfile = "external-redis"
$ApiBase = "http://127.0.0.1:18000"
```

这里真正还需要你自己替换的只有两项：

- `$ImageRepository` 里的 `<your-account>`
- `$RedisUrl` 里的 `<your-real-redis-host>`

### 3. 登录镜像仓库

如果你使用 GHCR：

```powershell
echo <your-ghcr-token> | docker login ghcr.io -u <your-account> --password-stdin
```

如果你使用其他仓库，把地址和账号改成对应值。

### 4. 构建并推送应用镜像

```powershell
docker build --pull -t "$ImageRepository`:$ImageTag" -f automation-hub/Dockerfile automation-hub
docker push "$ImageRepository`:$ImageTag"
```

如果你希望把 GHCR 登录、构建和推送合并成一段可直接执行的命令，推荐使用下面这组命令。注意：这里默认你已经撤销之前暴露过的旧 token，并重新生成了一枚新的 GHCR token。

```powershell
$GhcrUser = "<your-github-username>"
$Namespace = "automation-hub-test"
$Release = "automation-hub-test"
$ImageRepository = "ghcr.io/$GhcrUser/automation-hub"
$ImageTag = "test"

$SecureToken = Read-Host "Enter new GHCR token" -AsSecureString
$Bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureToken)
$GhcrToken = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($Bstr)

$GhcrToken | docker login ghcr.io -u $GhcrUser --password-stdin
docker build --pull -t "$ImageRepository`:$ImageTag" -f automation-hub/Dockerfile automation-hub
docker push "$ImageRepository`:$ImageTag"

[System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr)
Remove-Variable GhcrToken -ErrorAction SilentlyContinue
```

这段命令实际只保留了一个占位符：`<your-github-username>`。

### 5. 创建 namespace

```powershell
kubectl create namespace $Namespace
```

### 6. 创建 Secret

```powershell
kubectl -n $Namespace create secret generic $SecretName `
  --from-literal=ENVIRONMENT=test `
  --from-literal=DUMMY_SECRET=replace-me
```

如果 Secret 已存在，可先检查：

```powershell
kubectl -n $Namespace get secret $SecretName
```

### 7. 先做 Helm 渲染校验

```powershell
helm lint automation-hub/deploy/charts/automation-hub --values $ValuesFile
helm template $Release automation-hub/deploy/charts/automation-hub --namespace $Namespace --values $ValuesFile `
  --set image.repository=$ImageRepository `
  --set image.tag=$ImageTag `
  --set secret.existingSecretName=$SecretName `
  --set env.REDIS_URL=$RedisUrl > rendered-automation-hub-test.yaml
```

### 8. 安装或升级 release

```powershell
helm upgrade --install $Release automation-hub/deploy/charts/automation-hub `
  --namespace $Namespace `
  --create-namespace `
  --values $ValuesFile `
  --set image.repository=$ImageRepository `
  --set image.tag=$ImageTag `
  --set secret.existingSecretName=$SecretName `
  --set env.REDIS_URL=$RedisUrl
```

### 9. 等待 API 和 worker 就绪

```powershell
kubectl -n $Namespace get deployment
kubectl -n $Namespace rollout status deployment/$Release --timeout=180s
kubectl -n $Namespace rollout status deployment/$Release-worker --timeout=180s
kubectl -n $Namespace get pods -o wide
kubectl -n $Namespace get pvc
kubectl -n $Namespace get svc
```

### 10. 检查 worker 日志

```powershell
kubectl -n $Namespace logs deployment/$Release-worker --tail=200
```

### 11. 临时暴露 API 做验收

如果还没有 Ingress，就直接 port-forward：

```powershell
kubectl -n $Namespace port-forward service/$Release 18000:8000
```

然后新的终端里验证：

```powershell
Invoke-RestMethod -Method Get -Uri "$ApiBase/health"
```

### 12. 初始化管理员 token

如果当前是空库，执行：

```powershell
$bootstrap = Invoke-RestMethod -Method Post -Uri "$ApiBase/auth/bootstrap" `
  -ContentType 'application/json' `
  -Body '{"admin_name":"Admin","device_name":"Initial Device","device_platform":"windows"}'

$bootstrapToken = $bootstrap.token

$newToken = Invoke-RestMethod -Method Post -Uri "$ApiBase/auth/tokens" `
  -Headers @{ Authorization = "Bearer $bootstrapToken" } `
  -ContentType 'application/json' `
  -Body '{"template":"admin_ops"}'

$newToken.token | Set-Content automation-hub/.tmp_remote_live_token.txt -Encoding utf8
```

### 13. 验证 token

```powershell
$token = Get-Content automation-hub/.tmp_remote_live_token.txt -Raw
Invoke-RestMethod -Method Get -Uri "$ApiBase/auth/me" -Headers @{ Authorization = "Bearer $($token.Trim())" }
```

### 14. 执行 live 验收

```powershell
D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live --live-api-base $ApiBase --live-token-file automation-hub/.tmp_remote_live_token.txt
```

### 15. 如果后续要进入 worker live

普通环境当前先到 `live` 即可；只有在目标环境明确具备 Docker executor 前提时，才继续：

```powershell
D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live-worker --live-api-base $ApiBase --live-token-file automation-hub/.tmp_remote_live_token.txt --live-worker-executor docker
```

如果当前还没有确认 worker 节点支持 docker.sock，就先不要执行这一步。

## 三、构建并推送镜像

先构建，再推送到集群可拉取的镜像仓库。

```powershell
docker build --pull -t "$ImageRepository`:$ImageTag" -f automation-hub/Dockerfile automation-hub
docker push "$ImageRepository`:$ImageTag"
```

如果 API 和 worker 使用同一镜像，不需要分别构建两次。

## 四、创建命名空间

```powershell
kubectl create namespace $Namespace
```

如果 namespace 已存在，可忽略 `AlreadyExists`。

## 五、创建外部 Secret

chart 当前通过 `envFrom` 读取 Secret，因此 Secret 中的键会直接变成环境变量。

如果你已经在集群里有一个现成 Secret，可跳过这一步，只要确保名字与 values 文件中的 `secret.existingSecretName` 一致。

示例：

```powershell
kubectl -n $Namespace create secret generic $SecretName `
  --from-literal=ENVIRONMENT=production `
  --from-literal=DUMMY_SECRET=replace-me
```

说明：

- 当前应用没有强制要求某个特定 Secret 键才能启动。
- 真实环境不建议长期只放 `DUMMY_SECRET`；后续若有敏感配置，应继续通过这个 Secret 注入。
- values 文件里的 `secret.existingSecretName` 必须与这里一致。

## 六、准备环境专用 values 覆写文件

不建议直接改仓库里的生产模板。推荐在目标环境复制一份覆盖文件，例如：

```powershell
Copy-Item automation-hub/deploy/charts/automation-hub/values-prod-external-redis.yaml automation-hub/deploy/charts/automation-hub/values-staging.generated.yaml
```

或者 Docker executor 场景：

```powershell
Copy-Item automation-hub/deploy/charts/automation-hub/values-prod-docker-executor.yaml automation-hub/deploy/charts/automation-hub/values-staging.generated.yaml
```

至少要把以下值改成真实环境值：

- `image.repository`
- `image.tag`
- `env.REDIS_URL`
- `secret.existingSecretName`
- `persistence.size`

如果启用 Docker executor，还要确认：

- `worker.dockerSocket.enabled=true`
- `env.DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK="false"`
- worker 节点真的支持 `/var/run/docker.sock`

## 七、先做 Helm 校验

安装前先做 lint 和 template，避免渲染阶段才暴露问题。

```powershell
helm lint automation-hub/deploy/charts/automation-hub --values $ValuesFile
helm template $Release automation-hub/deploy/charts/automation-hub --namespace $Namespace --values $ValuesFile > rendered-$RuntimeProfile.yaml
```

建议重点确认：

- API Deployment 副本数是否仍为 1
- `REDIS_URL` 是否正确展开
- Secret 名称是否正确引用
- PVC 是否已启用
- Docker executor 场景下是否真的挂载了 `docker.sock`

## 八、执行 Helm 安装或升级

普通真实环境：

```powershell
helm upgrade --install $Release automation-hub/deploy/charts/automation-hub `
  --namespace $Namespace `
  --create-namespace `
  --values $ValuesFile `
  --set image.repository=$ImageRepository `
  --set image.tag=$ImageTag `
  --set secret.existingSecretName=$SecretName `
  --set env.REDIS_URL=$RedisUrl
```

如果你已经把这些值都写进环境专用 values 文件，可以去掉对应的 `--set`。

## 九、等待资源就绪

安装后不要立刻开始验收，先等核心资源进入可用状态。

```powershell
kubectl -n $Namespace rollout status deployment/$Release --timeout=180s
kubectl -n $Namespace rollout status deployment/$Release-worker --timeout=180s
kubectl -n $Namespace get pods -o wide
kubectl -n $Namespace get pvc
kubectl -n $Namespace get svc
```

如果使用 chart 内外部组件组合不同，deployment 名称可以先通过下面命令核对：

```powershell
kubectl -n $Namespace get deployment
```

## 十、检查 worker 日志与 Docker executor 前提

普通真实环境至少看 worker 是否正常监听 Redis。

```powershell
kubectl -n $Namespace logs deployment/$Release-worker --tail=200
```

如果是 Docker executor 场景，再额外确认：

```powershell
kubectl -n $Namespace describe deployment/$Release-worker
kubectl -n $Namespace logs deployment/$Release-worker --tail=200 | Select-String "docker|preflight|socket"
```

只要 worker 因 docker CLI、socket 或镜像预检失败退出，就不要进入 `live-worker` 验收。

## 十一、获取真实环境访问入口

chart 默认是 `ClusterIP` 服务，没有内置 Ingress。

你需要二选一：

1. 使用集群已有 Ingress / Gateway，把流量转到 service
2. 临时使用 `kubectl port-forward` 做远端验收

临时 port-forward 示例：

```powershell
kubectl -n $Namespace port-forward service/$Release 18000:8000
```

然后把 `http://127.0.0.1:18000` 作为 `live_api_base`。

如果你已有外部域名或网关地址，则直接使用真实地址作为 `live_api_base`。

## 十二、初始化或准备管理员 token

如果目标环境是空库，首次启动后需要先执行一次 bootstrap，再签发长期管理员 token。

Bootstrap 示例：

```powershell
$ApiBase = "http://127.0.0.1:18000"

$bootstrap = Invoke-RestMethod -Method Post -Uri "$ApiBase/auth/bootstrap" `
  -ContentType 'application/json' `
  -Body '{"admin_name":"Admin","device_name":"Initial Device","device_platform":"windows"}'

$bootstrapToken = $bootstrap.token
```

签发长期管理员 token：

```powershell
$newToken = Invoke-RestMethod -Method Post -Uri "$ApiBase/auth/tokens" `
  -Headers @{ Authorization = "Bearer $bootstrapToken" } `
  -ContentType 'application/json' `
  -Body '{"template":"admin_ops"}'

$newToken.token | Set-Content automation-hub/.tmp_remote_live_token.txt -Encoding utf8
```

建议再验证一次：

```powershell
$token = Get-Content automation-hub/.tmp_remote_live_token.txt -Raw
Invoke-RestMethod -Method Get -Uri "$ApiBase/auth/me" -Headers @{ Authorization = "Bearer $($token.Trim())" }
```

## 十三、执行真实环境验收

部署完成后，按固定顺序执行：先 `live`，再 `live-worker`。

```powershell
D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live --live-api-base $ApiBase --live-token-file automation-hub/.tmp_remote_live_token.txt

D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live-worker --live-api-base $ApiBase --live-token-file automation-hub/.tmp_remote_live_token.txt --live-worker-executor docker
```

执行时同步对照以下文档：

- [remote-acceptance-checklist.md](remote-acceptance-checklist.md)
- [live-acceptance-2026-03-11-remote.md](live-acceptance-2026-03-11-remote.md)
- [worker-live-acceptance-2026-03-11-remote.md](worker-live-acceptance-2026-03-11-remote.md)

## 十四、发布前最小检查点

在认定真实环境部署完成前，至少确认：

- [ ] Helm 安装成功
- [ ] API deployment 可用
- [ ] worker deployment 可用
- [ ] PVC 绑定成功
- [ ] `GET /health` 返回 200
- [ ] `GET /auth/me` 返回 200
- [ ] `live` 成功并留档
- [ ] `live-worker --live-worker-executor docker` 成功并留档

## 十五、回滚入口

如果新版本部署失败，至少保留下面两个入口：

```powershell
helm -n $Namespace history $Release
helm -n $Namespace rollback $Release <REVISION>
```

如果失败与镜像或 values 有关，不要先删 PVC；先保留数据库和运行产物，便于回溯。