import { Injectable, Logger } from '@nestjs/common';
import { v2 as cloudinary } from 'cloudinary';
import { ConfigService } from '@nestjs/config';

interface CloudinaryError {
  message: string;
  http_code?: number;
  name?: string;
}

@Injectable()
export class CloudinaryService {
  private readonly logger = new Logger(CloudinaryService.name);
  private readonly UPLOAD_TIMEOUT = 120000; // 2 minutes per image
  private readonly MAX_RETRIES = 3;

  constructor(private configService: ConfigService) {
    cloudinary.config({
      cloud_name: this.configService.get('CLOUDINARY_CLOUD_NAME'),
      api_key: this.configService.get('CLOUDINARY_API_KEY'),
      api_secret: this.configService.get('CLOUDINARY_API_SECRET'),
      timeout: 120000, // 2 minutes timeout for Cloudinary API calls
    });
  }

  /**
   * Helper function to add timeout to a Promise
   */
  private withTimeout<T>(
    promise: Promise<T>,
    timeoutMs: number,
    errorMessage: string,
  ): Promise<T> {
    return Promise.race([
      promise,
      new Promise<T>((_, reject) =>
        setTimeout(() => reject(new Error(errorMessage)), timeoutMs),
      ),
    ]);
  }

  async uploadImage(
    file: Express.Multer.File,
    folder: string = 'photoboth',
    retryCount: number = 0,
  ): Promise<string> {
    const uploadPromise = new Promise<string>((resolve, reject) => {
      const uploadStream = cloudinary.uploader.upload_stream(
        {
          folder: folder,
          resource_type: 'auto',
          transformation: [{ quality: 'auto' }, { fetch_format: 'auto' }],
          timeout: 120000, // 2 minutes
        },
        (error, result) => {
          if (error) {
            // Preserve Cloudinary error details
            const cloudinaryError = error as CloudinaryError;
            const errorMessage = cloudinaryError.http_code
              ? `Cloudinary Error [${cloudinaryError.http_code}]: ${cloudinaryError.message || error.message}`
              : `Cloudinary Error: ${error.message}`;
            reject(new Error(errorMessage));
          } else if (!result) {
            reject(new Error('Upload completed but no result returned'));
          } else {
            resolve(result.secure_url);
          }
        },
      );

      uploadStream.on('error', (err) => {
        const errorMessage = err instanceof Error ? err.message : String(err);
        reject(new Error(`Cloudinary Upload Stream Error: ${errorMessage}`));
      });

      uploadStream.end(file.buffer);
    });

    try {
      // Wrap with timeout
      return await this.withTimeout(
        uploadPromise,
        this.UPLOAD_TIMEOUT,
        `Upload timeout after ${this.UPLOAD_TIMEOUT}ms`,
      );
    } catch (error) {
      // Preserve original error message from Cloudinary
      const errorMessage =
        error instanceof Error ? error.message : 'Unknown error';
      this.logger.warn(
        `Upload attempt ${retryCount + 1} failed: ${errorMessage}`,
      );

      // Retry logic
      if (retryCount < this.MAX_RETRIES - 1) {
        this.logger.log(
          `Retrying upload (attempt ${retryCount + 2}/${this.MAX_RETRIES})...`,
        );
        // Wait 2 seconds before retry
        await new Promise((resolve) => setTimeout(resolve, 2000));
        return this.uploadImage(file, folder, retryCount + 1);
      }

      // All retries failed - throw original error to preserve Cloudinary error details
      throw error;
    }
  }

  async deleteImage(publicId: string): Promise<void> {
    try {
      await cloudinary.uploader.destroy(publicId);
    } catch (error) {
      // Preserve Cloudinary error details
      const cloudinaryError = error as CloudinaryError;
      const baseMessage =
        cloudinaryError.message ||
        (error instanceof Error ? error.message : String(error));
      const errorMessage = cloudinaryError.http_code
        ? `Cloudinary Error [${cloudinaryError.http_code}]: ${baseMessage}`
        : baseMessage;
      throw new Error(errorMessage);
    }
  }

  extractPublicId(url: string): string {
    const parts = url.split('/');
    const filename = parts[parts.length - 1];
    return filename.split('.')[0];
  }
}
