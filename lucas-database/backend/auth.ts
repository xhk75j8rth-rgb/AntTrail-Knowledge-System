import type { NextFunction, Request, Response } from 'express';
import { ApiError } from './errors';
import { checkBearerToken, touchDatabaseTokenUsage } from './tokenService';

export const requireWriteAuth = (req: Request, _res: Response, next: NextFunction) => {
  const header = req.header('authorization') || '';
  const match = header.match(/^Bearer\s+(.+)$/i);

  if (!match) {
    return next(new ApiError('TOKEN_MISSING', 'Authorization bearer token is required', 401));
  }

  const result = checkBearerToken(match[1]);
  if (!result.valid) {
    return next(new ApiError('TOKEN_INVALID', 'Authorization bearer token is invalid', 403));
  }

  if (result.tokenId) {
    touchDatabaseTokenUsage(result.tokenId);
  }

  return next();
};
