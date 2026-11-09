interface QueuedJob<T> {
  priority: number;
  order: number;
  run: () => Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

export class TranslationQueue {
  private waiting: Array<QueuedJob<unknown>> = [];
  private running = 0;
  private order = 0;

  constructor(private concurrency = 1) {}

  setConcurrency(concurrency: number): void {
    this.concurrency = Math.max(1, concurrency);
    this.pump();
  }

  enqueue<T>(priority: number, run: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      this.waiting.push({ priority, order: this.order++, run, resolve, reject } as QueuedJob<unknown>);
      this.waiting.sort((left, right) => left.priority - right.priority || left.order - right.order);
      this.pump();
    });
  }

  private pump(): void {
    while (this.running < this.concurrency && this.waiting.length > 0) {
      const job = this.waiting.shift();
      if (!job) return;
      this.running += 1;
      job
        .run()
        .then(job.resolve, job.reject)
        .finally(() => {
          this.running -= 1;
          this.pump();
        });
    }
  }
}
