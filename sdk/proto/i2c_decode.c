/*
 * libproto — I2C decoder. See <duneos/i2c_decode.h>.
 */

#include "duneos/i2c_decode.h"

int i2c_decode(const i2c_sample_t *s, int n, int scl_bit, int sda_bit,
               i2c_token_t *out, int max)
{
    uint32_t m_scl = 1u << scl_bit, m_sda = 1u << sda_bit;
    int nt = 0;
    int scl = 1, sda = 1, active = 0, bits = 0, byte = 0, idx = 0;

    for (int i = 0; i < n && nt < max; i++) {
        uint32_t lv   = s[i].levels;
        int      nscl = (lv & m_scl) ? 1 : 0;
        int      nsda = (lv & m_sda) ? 1 : 0;
        int      pscl = scl, psda = sda;

        /* SDA moving while SCL stays high → START / STOP. */
        if (pscl == 1 && nscl == 1 && nsda != psda) {
            if (psda == 1 && nsda == 0) {
                out[nt++] = (i2c_token_t){ I2C_TOK_START, 0, 0, 0, s[i].t_us };
                active = 1; bits = 0; byte = 0; idx = 0;
            } else {
                out[nt++] = (i2c_token_t){ I2C_TOK_STOP, 0, 0, 0, s[i].t_us };
                active = 0;
            }
        }

        /* SCL rising edge → clock a bit (sample SDA). */
        if (pscl == 0 && nscl == 1 && active) {
            if (bits < 8) { byte = (byte << 1) | (nsda & 1); bits++; }
            else {                                       /* 9th clock = ACK */
                i2c_token_t t = { (uint8_t)(idx == 0 ? I2C_TOK_ADDR : I2C_TOK_DATA),
                                  0, 0, (uint8_t)(nsda == 0), s[i].t_us };
                if (idx == 0) { t.val = (uint8_t)(byte >> 1); t.rw = (uint8_t)(byte & 1); }
                else          { t.val = (uint8_t)byte; }
                out[nt++] = t;
                bits = 0; byte = 0; idx++;
            }
        }

        scl = nscl;
        sda = nsda;
    }
    return nt;
}
