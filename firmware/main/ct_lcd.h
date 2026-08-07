// จอ ILI9341 บน SPI2 + ไฟหลังแบบหรี่ได้
//
// ค่าคอนฟิกทั้งหมดมาจากการวัดบอร์ดจริงด้วย firmware/probe (ดู docs/hardware.md)
// ไม่ได้มาจากเลขรุ่นชิป เพราะจอตัวนี้ล็อกการอ่าน ID ไว้
#pragma once

#include <stdint.h>
#include <stddef.h>

void ct_lcd_init(void);

// ส่งพิกเซลลงจอ พิกัดรวมปลายทั้งสองข้าง ข้อมูลเป็น RGB565 เรียงไบต์แบบ big-endian
void ct_lcd_blit(int x1, int y1, int x2, int y2, const void *pixels, size_t bytes);

// เหมือน ct_lcd_blit แต่กำหนดขนาดก้อนต่อหนึ่ง SPI transaction เองได้ (chunk=0 = ส่งรวด
// เดียว) — ใช้ตอน bring-up เพื่อชี้ให้ชัดว่าขนาด transaction คือตัวแปรที่ทำภาพเป็นขยะ
void ct_lcd_blit_chunked(int x1, int y1, int x2, int y2, const void *pixels, size_t bytes,
                         size_t chunk);

// ความสว่าง 0..100 — 15% คือค่าที่ใช้ตอนจอว่าง
void ct_lcd_set_backlight(int percent);
int ct_lcd_backlight(void);
