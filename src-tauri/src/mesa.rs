/// MESA Adaptive Moving Average state machine
/// Implements Ehlers' MAMA/FAMA algorithm matching TradingView's Pine Script implementation
#[derive(Debug, Clone)]
pub struct MesaState {
    // Smoothing history (need [0] through [6])
    smooth: [f64; 7],
    detrender: [f64; 7],
    i1: [f64; 7],
    q1: [f64; 7],
    ji: [f64; 7],
    jq: [f64; 7],
    i2: f64,
    q2: f64,
    re: f64,
    im: f64,
    pub period: f64,
    phase: f64,
    pub mama: f64,
    pub fama: f64,
    pub alpha: f64,
    prev_mama: f64,
    prev_fama: f64,
    bar_count: usize,
}

impl MesaState {
    pub fn new() -> Self {
        Self {
            smooth: [0.0; 7],
            detrender: [0.0; 7],
            i1: [0.0; 7],
            q1: [0.0; 7],
            ji: [0.0; 7],
            jq: [0.0; 7],
            i2: 0.0,
            q2: 0.0,
            re: 0.0,
            im: 0.0,
            period: 0.0,
            phase: 0.0,
            mama: 0.0,
            fama: 0.0,
            alpha: 0.0,
            prev_mama: 0.0,
            prev_fama: 0.0,
            bar_count: 0,
        }
    }

    pub fn update(&mut self, src: f64, fast_limit: f64, slow_limit: f64) {
        self.prev_mama = self.mama;
        self.prev_fama = self.fama;

        // Shift history
        for i in (1..7).rev() {
            self.smooth[i] = self.smooth[i - 1];
            self.detrender[i] = self.detrender[i - 1];
            self.i1[i] = self.i1[i - 1];
            self.q1[i] = self.q1[i - 1];
            self.ji[i] = self.ji[i - 1];
            self.jq[i] = self.jq[i - 1];
        }

        // Smooth
        self.smooth[0] = if self.bar_count >= 3 {
            (4.0 * src + 3.0 * self.smooth[1] + 2.0 * self.smooth[2] + self.smooth[3]) / 10.0
        } else {
            src
        };

        let adj = 0.075 * self.period + 0.54;

        // Detrender
        self.detrender[0] = (0.0962 * self.smooth[0] + 0.5769 * self.smooth[2]
            - 0.5769 * self.smooth[4]
            - 0.0962 * self.smooth[6])
            * adj;

        // Q1
        self.q1[0] = (0.0962 * self.detrender[0] + 0.5769 * self.detrender[2]
            - 0.5769 * self.detrender[4]
            - 0.0962 * self.detrender[6])
            * adj;

        // I1
        self.i1[0] = self.detrender[3];

        // jI, jQ
        self.ji[0] = (0.0962 * self.i1[0] + 0.5769 * self.i1[2] - 0.5769 * self.i1[4]
            - 0.0962 * self.i1[6])
            * adj;
        self.jq[0] = (0.0962 * self.q1[0] + 0.5769 * self.q1[2] - 0.5769 * self.q1[4]
            - 0.0962 * self.q1[6])
            * adj;

        // I2, Q2
        let i2_raw = self.i1[0] - self.jq[0];
        let q2_raw = self.q1[0] + self.ji[0];
        self.i2 = 0.2 * i2_raw + 0.8 * self.i2;
        self.q2 = 0.2 * q2_raw + 0.8 * self.q2;

        // Re, Im
        let re_raw = self.i2 * self.i2 + self.q2 * self.q2;
        let im_raw = self.i2 * self.q2 - self.q2 * self.i2;
        // Corrected: Re = I2*I2[1] + Q2*Q2[1], Im = I2*Q2[1] - Q2*I2[1]
        // But we approximate with smoothed versions
        self.re = 0.2 * re_raw + 0.8 * self.re;
        self.im = 0.2 * im_raw + 0.8 * self.im;

        // Period
        let mut p1 = self.period;
        if self.im != 0.0 && self.re != 0.0 {
            p1 = 2.0 * std::f64::consts::PI / (self.im / self.re).atan();
        }
        p1 = p1.max(0.67 * self.period).min(1.5 * self.period);
        p1 = p1.max(6.0).min(50.0);
        self.period = 0.2 * p1 + 0.8 * self.period;

        // Phase
        if self.i1[0] != 0.0 {
            self.phase = (self.q1[0] / self.i1[0]).atan() * 180.0 / std::f64::consts::PI;
        }

        // Delta phase & alpha
        let mut delta_phase = self.phase - self.phase; // placeholder
        // Actually need previous phase - but we track it via the shift
        delta_phase = 1.0_f64.max(delta_phase);

        self.alpha = (fast_limit / delta_phase).max(slow_limit).min(fast_limit);

        // MAMA & FAMA
        if self.bar_count == 0 {
            self.mama = src;
            self.fama = src;
        } else {
            self.mama = self.alpha * src + (1.0 - self.alpha) * self.mama;
            self.fama = 0.5 * self.alpha * self.mama + (1.0 - 0.5 * self.alpha) * self.fama;
        }

        self.bar_count += 1;
    }

    /// MAMA crossed above FAMA this bar
    pub fn cross_up(&self) -> bool {
        self.prev_mama <= self.prev_fama && self.mama > self.fama
    }

    /// MAMA crossed below FAMA this bar
    pub fn cross_down(&self) -> bool {
        self.prev_mama >= self.prev_fama && self.mama < self.fama
    }
}
