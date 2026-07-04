import type { Response } from 'express';

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(code: string, message: string, status = 400) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

export const sendError = (res: Response, error: unknown, requestId: string) => {
  if (error instanceof ApiError) {
    return res.status(error.status).json({
      ok: false,
      error: {
        code: error.code,
        message: error.message,
        request_id: requestId
      }
    });
  }

  console.error(error);
  return res.status(500).json({
    ok: false,
    error: {
      code: 'INTERNAL_ERROR',
      message: 'Internal server error',
      request_id: requestId
    }
  });
};
