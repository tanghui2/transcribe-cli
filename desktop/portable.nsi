; NSIS 脚本：把 Electron 应用目录打成单文件 portable exe
;
; 为什么不用 electron-builder：它在本机沙箱里跑不起来（子进程被静默终止，
; 无任何输出，exit=0）。而 NSIS 能正常跑，手工做反而更可控。
;
; 为什么不用「运行时调 7za 解压」：7za 的路径含 @ 与特殊字符，
; nsExec 调用容易静默失败（实测 exe 起来就退）。
; 改用 NSIS 原生的 File /r —— 解压由 NSIS 自己完成，不依赖外部程序。
;
; 原理：
;   1. 打包时用 File /r 把 app 目录整个嵌进 exe
;   2. 运行时解到 %TEMP% 下的唯一目录
;   3. 从中运行主 exe，等它退出后清理临时目录
;
; 用法：
;   makensis /DAPPDIR="<应用目录绝对路径>" /DOUTFILE="<输出 exe 名>" portable.nsi

!define APP_NAME "transcribe-cli"
!define APP_EXE "transcribe-cli.exe"

!ifndef APPDIR
  !define APPDIR "app"
!endif
!ifndef OUTFILE
  !define OUTFILE "transcribe-cli-0.1.0-windows-x64.exe"
!endif

Name "${APP_NAME}"
OutFile "${OUTFILE}"
RequestExecutionLevel user
ShowInstDetails hide
ShowUninstDetails hide
SilentInstall silent

; 压缩算法要在全局设置（SetCompressor 不能写在 Section 里）
SetCompressor /SOLID lzma

VIProductVersion "0.1.0.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "FileDescription" "${APP_NAME} 本地音视频转写"
VIAddVersionKey "FileVersion" "0.1.0.0"
VIAddVersionKey "ProductVersion" "0.1.0.0"

Var TMPDIR

Function .onInit
  ; 每次运行解压到独立临时目录，避免多开时互相覆盖。
  ; NSIS 没有 $RANDOM，用 GetTempFileName 生成唯一名（签名：GetTempFileName $out [base_dir]）
  GetTempFileName "$TMPDIR" "$TEMP"
  Delete "$TMPDIR"
  CreateDirectory "$TMPDIR"
FunctionEnd

Section "extract"
  ; 解压整个应用目录（NSIS 原生处理，不依赖外部解压程序）
  SetOutPath "$TMPDIR"
  File /r "${APPDIR}\*"

  ; 运行主程序，等它退出。
  ; 注意：必须让工作目录 = 解压目录。Electron 用相对路径找 resources/，
  ; 而 NSIS 的 Exec 默认用「NSIS 自身的工作目录」，会导致主进程启动即退出
  ; （实测：exe 存在、asar 正常、Exec 后 8 秒进程就没了）。
  ; SetOutPath 同时决定后续 Exec 的工作目录。
  ExecWait '"$TMPDIR\${APP_EXE}"'

  ; 清理临时目录（失败也无妨）
  RMDir /r "$TMPDIR"
SectionEnd
