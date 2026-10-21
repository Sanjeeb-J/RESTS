/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define RESTS_RX_RING_SIZE 128U
#define RESTS_COMMAND_SIZE 96U
#define RESTS_AUDIO_MAGIC_0 0xA5U
#define RESTS_AUDIO_MAGIC_1 0x5AU
#define RESTS_AUDIO_TYPE 1U
#define RESTS_AUDIO_HEADER_SIZE 7U
#define RESTS_AUDIO_MAX_PAYLOAD 512U
#define RESTS_AUDIO_FRAME_SIZE (RESTS_AUDIO_HEADER_SIZE + RESTS_AUDIO_MAX_PAYLOAD + 2U)
#define RESTS_AUDIO_TIMEOUT_MS 2000U
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
UART_HandleTypeDef huart1;

/* USER CODE BEGIN PV */
static uint8_t usart1_rx_byte;
static volatile uint16_t usart1_rx_head;
static volatile uint16_t usart1_rx_tail;
static volatile uint8_t usart1_rx_overflow;
static volatile uint8_t usart1_error;
static volatile uint8_t usart1_rx_ring[RESTS_RX_RING_SIZE];
static char rests_command[RESTS_COMMAND_SIZE];
static uint16_t rests_command_length;
static uint8_t rests_command_overflow;
static uint8_t rests_audio_frame[RESTS_AUDIO_FRAME_SIZE];
static uint16_t rests_audio_frame_length;
static uint16_t rests_audio_expected_length;
static uint32_t rests_audio_last_byte_tick;
static uint8_t rests_audio_mode;
static uint8_t rests_audio_magic_pending;
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART1_UART_Init(void);
/* USER CODE BEGIN PFP */
static void USART1_StartReceive(void);
static void RESTS_ProcessRx(void);
static void RESTS_ProcessByte(uint8_t byte);
static void RESTS_ProcessCommandByte(uint8_t byte);
static uint16_t RESTS_Crc16(const uint8_t *data, uint16_t length);
static void RESTS_HandleCommand(const char *command);
static void RESTS_Send(const char *message);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
static void USART1_StartReceive(void)
{
  if ((usart1_error == 0U) &&
      (HAL_UART_Receive_IT(&huart1, &usart1_rx_byte, 1U) != HAL_OK))
  {
    usart1_error = 1U;
  }
}

static void RESTS_Send(const char *message)
{
  uint16_t length = (uint16_t)strlen(message);

  if (HAL_UART_Transmit(&huart1, (uint8_t *)message, length, 100U) != HAL_OK)
  {
    usart1_error = 1U;
  }
}

static void RESTS_HandleCommand(const char *command)
{
  const char *separator;

  if (strcmp(command, "HELLO?") == 0)
  {
    RESTS_Send("RESTS/1 READY MCU=STM32F429ZI\r\n");
  }
  else if (strcmp(command, "PING") == 0)
  {
    RESTS_Send("PONG\r\n");
  }
  else if (strcmp(command, "PTT:BEGIN") == 0)
  {
    RESTS_Send("ACK:PTT:BEGIN\r\n");
  }
  else if (strcmp(command, "PTT:END") == 0)
  {
    RESTS_Send("ACK:PTT:END\r\n");
  }
  else if (strncmp(command, "LANG:", 5U) == 0)
  {
    separator = strchr(command + 5, ':');
    if ((separator != NULL) && (separator != command + 5) &&
        (separator[1] != '\0') && (strchr(separator + 1, ':') == NULL))
    {
      RESTS_Send("ACK:LANG\r\n");
    }
    else
    {
      RESTS_Send("ERR:BAD_LANG\r\n");
    }
  }
  else
  {
    RESTS_Send("ERR:UNKNOWN_CMD\r\n");
  }
}

static uint16_t RESTS_Crc16(const uint8_t *data, uint16_t length)
{
  uint16_t crc = 0xFFFFU;
  uint16_t i;
  uint8_t bit;

  for (i = 0U; i < length; i++)
  {
    crc ^= (uint16_t)data[i] << 8;
    for (bit = 0U; bit < 8U; bit++)
    {
      if ((crc & 0x8000U) != 0U)
      {
        crc = (uint16_t)((crc << 1) ^ 0x1021U);
      }
      else
      {
        crc = (uint16_t)(crc << 1);
      }
    }
  }
  return crc;
}

static void RESTS_ProcessCommandByte(uint8_t byte)
{
  if ((byte == (uint8_t)'\r') || (byte == (uint8_t)'\n'))
  {
    if (rests_command_overflow != 0U)
    {
      RESTS_Send("ERR:LINE_TOO_LONG\r\n");
    }
    else if (rests_command_length != 0U)
    {
      rests_command[rests_command_length] = '\0';
      RESTS_HandleCommand(rests_command);
    }

    rests_command_length = 0U;
    rests_command_overflow = 0U;
    return;
  }

  if (rests_command_overflow == 0U)
  {
    if (rests_command_length < (RESTS_COMMAND_SIZE - 1U))
    {
      rests_command[rests_command_length] = (char)byte;
      rests_command_length++;
    }
    else
    {
      rests_command_overflow = 1U;
    }
  }
}

static void RESTS_ProcessByte(uint8_t byte)
{
  uint16_t payload_length;
  uint16_t received_crc;
  uint16_t calculated_crc;

  if (rests_audio_mode == 0U)
  {
    if (rests_audio_magic_pending != 0U)
    {
      rests_audio_magic_pending = 0U;
      if (byte == RESTS_AUDIO_MAGIC_1)
      {
        rests_audio_frame[0] = RESTS_AUDIO_MAGIC_0;
        rests_audio_frame[1] = RESTS_AUDIO_MAGIC_1;
        rests_audio_frame_length = 2U;
        rests_audio_expected_length = 0U;
        rests_audio_mode = 1U;
        rests_audio_last_byte_tick = HAL_GetTick();
        return;
      }
      RESTS_ProcessCommandByte(RESTS_AUDIO_MAGIC_0);
    }

    if (byte == RESTS_AUDIO_MAGIC_0)
    {
      rests_audio_magic_pending = 1U;
      rests_audio_last_byte_tick = HAL_GetTick();
      return;
    }
    RESTS_ProcessCommandByte(byte);
    return;
  }

  rests_audio_last_byte_tick = HAL_GetTick();
  if (rests_audio_frame_length < RESTS_AUDIO_FRAME_SIZE)
  {
    rests_audio_frame[rests_audio_frame_length++] = byte;
  }
  else
  {
    rests_audio_mode = 0U;
    rests_audio_frame_length = 0U;
    rests_audio_expected_length = 0U;
    RESTS_Send("ERR:AUDIO_FRAME\r\n");
    return;
  }

  if (rests_audio_frame_length == RESTS_AUDIO_HEADER_SIZE)
  {
    payload_length = (uint16_t)rests_audio_frame[5] |
                     ((uint16_t)rests_audio_frame[6] << 8);
    if ((rests_audio_frame[2] != RESTS_AUDIO_TYPE) ||
        (payload_length == 0U) || (payload_length > RESTS_AUDIO_MAX_PAYLOAD))
    {
      rests_audio_mode = 0U;
      rests_audio_frame_length = 0U;
      rests_audio_expected_length = 0U;
      RESTS_Send("ERR:AUDIO_HEADER\r\n");
      return;
    }
    rests_audio_expected_length = RESTS_AUDIO_HEADER_SIZE + payload_length + 2U;
  }

  if ((rests_audio_expected_length != 0U) &&
      (rests_audio_frame_length == rests_audio_expected_length))
  {
    received_crc = (uint16_t)rests_audio_frame[rests_audio_expected_length - 2U] |
                   ((uint16_t)rests_audio_frame[rests_audio_expected_length - 1U] << 8);
    calculated_crc = RESTS_Crc16(&rests_audio_frame[2],
                                  (uint16_t)(rests_audio_expected_length - 4U));
    if (received_crc == calculated_crc)
    {
      if (HAL_UART_Transmit(&huart1, rests_audio_frame,
                            rests_audio_expected_length, 1000U) != HAL_OK)
      {
        usart1_error = 1U;
      }
    }
    else
    {
      RESTS_Send("ERR:AUDIO_CRC\r\n");
    }
    rests_audio_mode = 0U;
    rests_audio_frame_length = 0U;
    rests_audio_expected_length = 0U;
  }
}

static void RESTS_ProcessRx(void)
{
  if (((rests_audio_mode != 0U) || (rests_audio_magic_pending != 0U)) &&
      ((HAL_GetTick() - rests_audio_last_byte_tick) > RESTS_AUDIO_TIMEOUT_MS))
  {
    rests_audio_mode = 0U;
    rests_audio_magic_pending = 0U;
    rests_audio_frame_length = 0U;
    rests_audio_expected_length = 0U;
  }

  if ((usart1_error != 0U) && (huart1.RxState == HAL_UART_STATE_READY))
  {
    usart1_error = 0U;
    USART1_StartReceive();
  }

  if (usart1_rx_overflow != 0U)
  {
    __disable_irq();
    usart1_rx_tail = usart1_rx_head;
    usart1_rx_overflow = 0U;
    __enable_irq();

    rests_command_length = 0U;
    rests_command_overflow = 0U;
    rests_audio_mode = 0U;
    rests_audio_magic_pending = 0U;
    rests_audio_frame_length = 0U;
    rests_audio_expected_length = 0U;
    RESTS_Send("ERR:RX_OVERFLOW\r\n");
  }

  while (usart1_rx_tail != usart1_rx_head)
  {
    uint16_t tail = usart1_rx_tail;
    uint8_t byte = usart1_rx_ring[tail];

    tail++;
    if (tail >= RESTS_RX_RING_SIZE)
    {
      tail = 0U;
    }
    usart1_rx_tail = tail;
    RESTS_ProcessByte(byte);
  }
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USART1_UART_Init();
  /* USER CODE BEGIN 2 */
  USART1_StartReceive();
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
    RESTS_ProcessRx();
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE3);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_0) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief USART1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART1_UART_Init(void)
{

  /* USER CODE BEGIN USART1_Init 0 */

  /* USER CODE END USART1_Init 0 */

  /* USER CODE BEGIN USART1_Init 1 */

  /* USER CODE END USART1_Init 1 */
  huart1.Instance = USART1;
  huart1.Init.BaudRate = 115200;
  huart1.Init.WordLength = UART_WORDLENGTH_8B;
  huart1.Init.StopBits = UART_STOPBITS_1;
  huart1.Init.Parity = UART_PARITY_NONE;
  huart1.Init.Mode = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART1_Init 2 */

  /* USER CODE END USART1_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOA_CLK_ENABLE();

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if ((huart->Instance == USART1) && (usart1_error == 0U))
  {
    uint16_t head = usart1_rx_head;
    uint16_t next = head + 1U;

    if (next >= RESTS_RX_RING_SIZE)
    {
      next = 0U;
    }

    if (next != usart1_rx_tail)
    {
      usart1_rx_ring[head] = usart1_rx_byte;
      usart1_rx_head = next;
    }
    else
    {
      usart1_rx_overflow = 1U;
    }

    USART1_StartReceive();
  }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART1)
  {
    usart1_error = 1U;
  }
}
/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
