*** Variables ***
${PLATFORM}    C:/Program Files/Renode/platforms/cpus/stm32f429.repl
${ELF}         ${CURDIR}/../Debug/RESTS_STM32.elf

*** Test Cases ***
USART1 Protocol Commands
    Execute Command    using sysbus
    Execute Command    mach create "rests-f429"
    Execute Command    machine LoadPlatformDescription "${PLATFORM}"
    Execute Command    sysbus LoadELF "${ELF}"
    Create Terminal Tester    sysbus.usart1    timeout=10
    Start Emulation
    Write Line To Uart    HELLO?    waitForEcho=false
    Wait For Line On Uart    RESTS/1 READY MCU=STM32F429ZI
    Write Line To Uart    PING    waitForEcho=false
    Wait For Line On Uart    PONG
    Write Line To Uart    PTT:BEGIN    waitForEcho=false
    Wait For Line On Uart    ACK:PTT:BEGIN
    Write Line To Uart    PTT:END    waitForEcho=false
    Wait For Line On Uart    ACK:PTT:END
    Write Line To Uart    LANG:en:ml    waitForEcho=false
    Wait For Line On Uart    ACK:LANG
    Write Line To Uart    UNKNOWN    waitForEcho=false
    Wait For Line On Uart    ERR:UNKNOWN_CMD
    Write To Uart    PING\r
    Wait For Line On Uart    PONG