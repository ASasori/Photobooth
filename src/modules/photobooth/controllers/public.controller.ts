import {
  Controller,
  Get,
  Post,
  Put,
  Param,
  Body,
  Query,
  HttpCode,
  HttpStatus,
  UseGuards,
  Request,
  UseInterceptors,
  UploadedFile,
  UploadedFiles,
  ParseFilePipe,
  MaxFileSizeValidator,
  FileTypeValidator,
  Delete,
  UnauthorizedException,
  BadRequestException,
  Logger,
} from '@nestjs/common';
import { FileInterceptor, FilesInterceptor } from '@nestjs/platform-express';
import {
  ApiTags,
  ApiOperation,
  ApiResponse,
  ApiParam,
  ApiBody,
  ApiQuery,
  ApiConsumes,
} from '@nestjs/swagger';
import { SessionService } from '../services/session.service';
import { PhotoService } from '../services/photo.service';
import { PhotoboothService } from '../services/photobooth.service';
import { CloudinaryService } from '../../photo/services/cloudinary.service';
import { PhotoboothGateway } from '../gateways/photobooth.gateway';
import { UserService } from '../../user/user.service';
import {
  CreateSessionDto,
  StartSessionDto,
  CompleteSessionDto,
  SessionResponseDto,
  AddFilterDto,
  ChangeFilterDto,
} from '../dto/session.dto';
import { CreatePhotoDto, PhotoResponseDto } from '../dto/photo.dto';
import { PhotoboothResponseDto } from '../dto/photobooth.dto';
import {
  PaginationDto,
  PaginatedResponseDto,
} from '../../../common/dto/pagination.dto';
import { SessionStatus } from '../enums/session-status.enum';
import { JwtAuthGuard } from '../../auth/guards/jwt-auth.guard';
import { Public } from '../../auth/decorators/public.decorator';
import { ApiBearerAuth } from '@nestjs/swagger';
import { User } from '../../user/entities/user.entity';

@ApiTags('public-photobooth')
@Controller('api/v1/photobooth')
@UseGuards(JwtAuthGuard)
@ApiBearerAuth('JWT-auth')
export class PublicController {
  private readonly logger = new Logger(PublicController.name);

  constructor(
    private readonly sessionService: SessionService,
    private readonly photoService: PhotoService,
    private readonly photoboothService: PhotoboothService,
    private readonly cloudinaryService: CloudinaryService,
    private readonly photoboothGateway: PhotoboothGateway,
    private readonly userService: UserService,
  ) {}

  @Get('status')
  @ApiOperation({ summary: 'Get photobooth system status' })
  @ApiResponse({
    status: 200,
    description: 'System status retrieved successfully',
    schema: {
      type: 'object',
      properties: {
        photobooths: {
          type: 'object',
          properties: {
            total: { type: 'number' },
            available: { type: 'number' },
            busy: { type: 'number' },
            maintenance: { type: 'number' },
            offline: { type: 'number' },
          },
        },
        sessions: {
          type: 'object',
          properties: {
            total: { type: 'number' },
            pending: { type: 'number' },
            active: { type: 'number' },
            completed: { type: 'number' },
            cancelled: { type: 'number' },
            expired: { type: 'number' },
          },
        },
      },
    },
  })
  async getSystemStatus() {
    const [photobooths, sessions] = await Promise.all([
      this.photoboothService.getStatus(),
      this.sessionService.getSessionStats(),
    ]);

    return {
      photobooths,
      sessions,
    };
  }

  @Get('available')
  @ApiOperation({ summary: 'Get available photobooths' })
  @ApiResponse({
    status: 200,
    description: 'Available photobooths retrieved successfully',
    type: [PhotoboothResponseDto],
  })
  async getAvailablePhotobooths() {
    return this.photoboothService.findAvailable();
  }

  @Get('sessions/current')
  @ApiOperation({
    summary: 'Get current active session for authenticated user',
    description:
      'Returns the most recent PENDING or ACTIVE session for the authenticated user. Returns null if no active session exists.',
  })
  @ApiResponse({
    status: 200,
    description: 'Current session retrieved successfully',
    type: SessionResponseDto,
  })
  @ApiResponse({
    status: 200,
    description: 'No active session found',
    schema: {
      type: 'object',
      properties: {
        message: { type: 'string', example: 'No active session found' },
        session: { type: 'null' },
      },
    },
  })
  @ApiResponse({ status: 401, description: 'Unauthorized' })
  async getCurrentSession(@Request() req: { user?: User }) {
    const userId = req.user?.id;
    if (!userId) {
      throw new UnauthorizedException('User ID not found in request');
    }

    const session = await this.sessionService.findCurrentSession(userId);

    if (!session) {
      return {
        message: 'No active session found',
        session: null,
      };
    }

    return session;
  }

  @Get('sessions')
  @ApiOperation({
    summary: 'Get all sessions for authenticated user',
    description:
      'Returns paginated list of sessions belonging to the authenticated user. Can filter by status.',
  })
  @ApiQuery({
    name: 'page',
    required: false,
    type: Number,
    description: 'Page number (starts from 1)',
    example: 1,
  })
  @ApiQuery({
    name: 'limit',
    required: false,
    type: Number,
    description: 'Number of items per page (max 100)',
    example: 10,
  })
  @ApiQuery({
    name: 'search',
    required: false,
    type: String,
    description: 'Search term for filtering sessions by notes',
  })
  @ApiQuery({
    name: 'status',
    required: false,
    enum: SessionStatus,
    description: 'Filter sessions by status',
    example: SessionStatus.ACTIVE,
  })
  @ApiResponse({
    status: 200,
    description: 'Paginated list of sessions retrieved successfully',
    type: PaginatedResponseDto<SessionResponseDto>,
  })
  @ApiResponse({ status: 401, description: 'Unauthorized' })
  async getUserSessions(
    @Request() req: { user?: User },
    @Query() paginationDto: PaginationDto,
    @Query('status') status?: SessionStatus,
  ) {
    const userId = req.user?.id;
    if (!userId) {
      throw new UnauthorizedException('User ID not found in request');
    }

    return await this.sessionService.findUserSessions(
      userId,
      paginationDto,
      status,
    );
  }

  @Post('sessions')
  @ApiOperation({
    summary: 'Create new photo session (requires authentication)',
    description:
      'Creates a new photo session. Deducts 10000 points from user account. User must have at least 10000 points to create a session.',
  })
  @ApiBody({ type: CreateSessionDto })
  @ApiResponse({
    status: 201,
    description: 'Session created successfully and 10000 points deducted',
    type: SessionResponseDto,
  })
  @ApiResponse({
    status: 400,
    description:
      'Bad request - Insufficient points (user must have at least 10000 points)',
  })
  @ApiResponse({ status: 401, description: 'Unauthorized' })
  @ApiResponse({
    status: 409,
    description:
      'Conflict - Photobooth not available or user has active session',
  })
  async createSession(
    @Body() createSessionDto: CreateSessionDto,
    @Request() req: { user?: User },
  ) {
    const userId = req.user?.id;
    if (!userId) {
      throw new UnauthorizedException('User ID not found in request');
    }

    // Deduct 10000 points before creating session
    const SESSION_COST = 10000;
    await this.userService.deductPoints(userId, SESSION_COST);

    try {
      const session = await this.sessionService.create(
        createSessionDto,
        userId,
      );

      // Emit WebSocket message to all connected clients
      this.photoboothGateway.emitStartSession(userId, session.id);

      return session;
    } catch (error) {
      // If session creation fails, refund the points
      await this.userService.addPoints(userId, { points: SESSION_COST });
      throw error;
    }
  }

  @Get('sessions/:id')
  @ApiOperation({ summary: 'Get session by ID' })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiResponse({
    status: 200,
    description: 'Session retrieved successfully',
    type: SessionResponseDto,
  })
  @ApiResponse({ status: 404, description: 'Session not found' })
  async getSession(@Param('id') id: string) {
    return this.sessionService.findOne(id);
  }

  @Put('sessions/:id/start')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'Start photo session' })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiBody({ type: StartSessionDto })
  @ApiResponse({
    status: 200,
    description: 'Session started successfully',
    type: SessionResponseDto,
  })
  @ApiResponse({ status: 400, description: 'Bad request' })
  @ApiResponse({ status: 404, description: 'Session not found' })
  async startSession(
    @Param('id') id: string,
    @Body() startSessionDto: StartSessionDto,
  ) {
    return this.sessionService.startSession(id, startSessionDto);
  }

  @Put('sessions/:id/complete')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'Complete photo session' })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiBody({ type: CompleteSessionDto })
  @ApiResponse({
    status: 200,
    description: 'Session completed successfully',
    type: SessionResponseDto,
  })
  @ApiResponse({ status: 400, description: 'Bad request' })
  @ApiResponse({ status: 404, description: 'Session not found' })
  async completeSession(
    @Param('id') id: string,
    @Body() completeSessionDto: CompleteSessionDto,
  ) {
    // Get session info before completing to emit WebSocket message
    const session = await this.sessionService.findOne(id);
    const userId = session.userId;

    // Complete session
    const result = await this.sessionService.completeSession(
      id,
      completeSessionDto,
    );

    // Emit WebSocket message to all connected clients
    if (userId) {
      this.photoboothGateway.emitStopSession(userId);
    }

    return result;
  }

  @Put('sessions/:id/cancel')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'Cancel photo session' })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiResponse({
    status: 200,
    description: 'Session cancelled successfully',
    type: SessionResponseDto,
  })
  @ApiResponse({ status: 400, description: 'Bad request' })
  @ApiResponse({ status: 404, description: 'Session not found' })
  async cancelSession(@Param('id') id: string) {
    // Get session info before cancelling to emit WebSocket message
    const session = await this.sessionService.findOne(id);
    const userId = session.userId;

    // Cancel session
    const result = await this.sessionService.cancelSession(id);

    // Emit WebSocket message to all connected clients
    if (userId) {
      this.photoboothGateway.emitStopSession(userId);
    }

    return result;
  }

  @Post('sessions/:id/filters')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'Add filter to session' })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiBody({ type: AddFilterDto })
  @ApiResponse({
    status: 200,
    description: 'Filter added to session successfully',
    type: SessionResponseDto,
  })
  @ApiResponse({ status: 400, description: 'Bad request' })
  @ApiResponse({ status: 404, description: 'Session not found' })
  @ApiResponse({ status: 409, description: 'Filter already exists in session' })
  async addFilter(@Param('id') id: string, @Body() addFilterDto: AddFilterDto) {
    // Add filter to session
    const result = await this.sessionService.addFilter(
      id,
      addFilterDto.filterId,
    );

    // Emit WebSocket message to all connected clients
    this.photoboothGateway.emitAddFilter(addFilterDto.filterId);

    return result;
  }

  @Delete('sessions/:id/filters/:filterId')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'Remove filter from session' })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiParam({
    name: 'filterId',
    description: 'Filter ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiResponse({
    status: 200,
    description: 'Filter removed from session successfully',
    type: SessionResponseDto,
  })
  @ApiResponse({ status: 400, description: 'Bad request' })
  @ApiResponse({ status: 404, description: 'Session or filter not found' })
  async deleteFilter(
    @Param('id') id: string,
    @Param('filterId') filterId: string,
  ) {
    // Remove filter from session
    const result = await this.sessionService.removeFilter(id, filterId);

    // Emit WebSocket message to all connected clients
    this.photoboothGateway.emitDeleteFilter(filterId);

    return result;
  }

  @Post('sessions/:id/start-capture')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({
    summary: 'Start capture for session',
    description: 'Emit WebSocket message to start capture for a session',
  })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiResponse({
    status: 200,
    description: 'Start capture message sent successfully',
    schema: {
      type: 'object',
      properties: {
        message: {
          type: 'string',
          example:
            'Start capture message sent for session 123e4567-e89b-12d3-a456-426614174000',
        },
        sessionId: {
          type: 'string',
          example: '123e4567-e89b-12d3-a456-426614174000',
        },
      },
    },
  })
  @ApiResponse({ status: 404, description: 'Session not found' })
  async startCapture(@Param('id') sessionId: string) {
    // Verify session exists
    await this.sessionService.findOne(sessionId);

    // Emit WebSocket message
    this.photoboothGateway.emitStartCapture(sessionId);

    return {
      message: `Start capture message sent for session ${sessionId}`,
      sessionId,
    };
  }

  @Post('sessions/:id/change-filter')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({
    summary: 'Add filter to session filterIds array',
    description:
      'Add filterId to session filterIds array and emit add_filter WebSocket message',
  })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiBody({ type: ChangeFilterDto })
  @ApiResponse({
    status: 200,
    description: 'Filter added to session successfully',
    type: SessionResponseDto,
  })
  @ApiResponse({
    status: 400,
    description:
      'Bad request - Cannot change filter for completed or cancelled session',
  })
  @ApiResponse({ status: 404, description: 'Session not found' })
  @ApiResponse({ status: 409, description: 'Filter already exists in session' })
  async changeFilter(
    @Param('id') sessionId: string,
    @Body() changeFilterDto: ChangeFilterDto,
  ) {
    // Add filter to session filterIds array
    const session = await this.sessionService.changeFilter(
      sessionId,
      changeFilterDto.filterId,
    );

    // Emit add_filter WebSocket message
    this.photoboothGateway.emitAddFilter(changeFilterDto.filterId);

    return session;
  }

  @Delete('sessions/:id/change-filter/:filterId')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({
    summary: 'Remove filter from session filterIds array',
    description:
      'Remove filterId from session filterIds array and emit remove_filter WebSocket message',
  })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiParam({
    name: 'filterId',
    description: 'Filter ID (UUID) to remove',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiResponse({
    status: 200,
    description: 'Filter removed from session successfully',
    type: SessionResponseDto,
  })
  @ApiResponse({
    status: 400,
    description:
      'Bad request - Cannot change filter for completed or cancelled session',
  })
  @ApiResponse({ status: 404, description: 'Session or filter not found' })
  async removeFilterFromChange(
    @Param('id') sessionId: string,
    @Param('filterId') filterId: string,
  ) {
    // Remove filter from session filterIds array
    const session = await this.sessionService.removeFilterFromChange(
      sessionId,
      filterId,
    );

    // Emit remove_filter WebSocket message
    this.photoboothGateway.emitDeleteFilter(filterId);

    return session;
  }

  @Get('sessions/:id/photos')
  @ApiOperation({ summary: 'Get session photos' })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiQuery({
    name: 'page',
    required: false,
    type: Number,
    description: 'Page number (starts from 1)',
  })
  @ApiQuery({
    name: 'limit',
    required: false,
    type: Number,
    description: 'Number of items per page (max 100)',
  })
  @ApiResponse({
    status: 200,
    description: 'Session photos retrieved successfully',
    type: PaginatedResponseDto<PhotoResponseDto>,
  })
  @ApiResponse({ status: 404, description: 'Session not found' })
  async getSessionPhotos(
    @Param('id') sessionId: string,
    @Query() paginationDto: PaginationDto,
  ) {
    return this.photoService.findBySessionPaginated(sessionId, paginationDto);
  }

  @Post('sessions/:id/photos')
  @ApiOperation({ summary: 'Add photo to session' })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiBody({ type: CreatePhotoDto })
  @ApiResponse({
    status: 201,
    description: 'Photo added successfully',
    type: PhotoResponseDto,
  })
  @ApiResponse({ status: 400, description: 'Bad request' })
  @ApiResponse({ status: 404, description: 'Session not found' })
  async addPhotoToSession(
    @Param('id') sessionId: string,
    @Body() createPhotoDto: CreatePhotoDto,
  ) {
    return this.photoService.create({
      ...createPhotoDto,
      sessionId,
    });
  }

  @Post('sessions/:id/photos/upload-multiple')
  @Public() // No JWT authentication required
  @UseInterceptors(
    FilesInterceptor('images', 20, {
      limits: {
        fileSize: 10 * 1024 * 1024, // 10MB per file
        files: 20, // Max 20 files
      },
    }),
  ) // Max 20 images, 10MB each
  @HttpCode(HttpStatus.CREATED)
  @ApiOperation({
    summary: 'Upload multiple images to session and complete session (Public)',
    description:
      'Upload multiple image files to a session. Images will be uploaded to Cloudinary and saved as Photo entities. After successful upload, the session will be automatically completed. Maximum 20 images per request. No authentication required.',
  })
  @ApiParam({
    name: 'id',
    description: 'Session ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiConsumes('multipart/form-data')
  @ApiBody({
    schema: {
      type: 'object',
      properties: {
        images: {
          type: 'array',
          items: {
            type: 'string',
            format: 'binary',
          },
          description: 'Image files to upload (max 20 files, 10MB each)',
        },
      },
      required: ['images'],
    },
  })
  @ApiResponse({
    status: 201,
    description: 'Images uploaded successfully and session completed',
    schema: {
      type: 'object',
      properties: {
        success: {
          type: 'boolean',
          example: true,
          description: 'Whether the operation was successful',
        },
        message: {
          type: 'string',
          example: 'Successfully uploaded 3 images and completed session',
        },
        uploaded: { type: 'number', example: 3 },
        failed: { type: 'number', example: 0 },
        sessionId: {
          type: 'string',
          example: '123e4567-e89b-12d3-a456-426614174000',
          description: 'Session ID',
        },
        errors: {
          type: 'array',
          items: { type: 'string' },
          description: 'Error messages for failed uploads (if any)',
        },
      },
    },
  })
  @ApiResponse({
    status: 400,
    description:
      'Bad request - No images provided, validation failed, or session has insufficient slots',
  })
  @ApiResponse({ status: 404, description: 'Session not found' })
  async uploadMultipleImages(
    @Param('id') sessionId: string,
    @UploadedFiles(
      new ParseFilePipe({
        validators: [
          new MaxFileSizeValidator({ maxSize: 10 * 1024 * 1024 }), // 10MB
          new FileTypeValidator({
            fileType: /^image\/(jpeg|jpg|png|gif|webp)$/,
          }),
        ],
        fileIsRequired: false,
      }),
    )
    files: Express.Multer.File[],
  ) {
    try {
      if (!files || files.length === 0) {
        throw new BadRequestException(
          'No images provided. Please upload at least one image.',
        );
      }

      // Verify session exists and is active
      const session = await this.sessionService.findOne(sessionId);
      if (!session) {
        throw new BadRequestException(`Session with ID ${sessionId} not found`);
      }

      if (session.status !== SessionStatus.ACTIVE) {
        throw new BadRequestException(
          'Cannot upload photos to inactive session',
        );
      }

      // Check if session has enough space for all images
      const remainingSlots = session.maxPhotos - session.photoCount;
      if (files.length > remainingSlots) {
        throw new BadRequestException(
          `Cannot upload ${files.length} images. Session only has ${remainingSlots} remaining slots (max: ${session.maxPhotos})`,
        );
      }

      // Get existing photos to calculate order
      const existingPhotos = await this.photoService.findBySession(sessionId);
      const baseOrder = existingPhotos.length;

      // Upload images
      const uploadedPhotos: PhotoResponseDto[] = [];
      const errors: string[] = [];

      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        try {
          const folder = `photobooth/sessions/${sessionId}`;
          const imageUrl = await this.cloudinaryService.uploadImage(
            file,
            folder,
          );
          const order = baseOrder + uploadedPhotos.length + 1;

          const photo = await this.photoService.create({
            sessionId,
            imageUrl,
            order,
          });

          uploadedPhotos.push(photo);
        } catch (error) {
          const errorMessage =
            error instanceof Error ? error.message : 'Unknown error';
          this.logger.error(`Failed to upload image ${i + 1}: ${errorMessage}`);
          errors.push(`Failed to upload image ${i + 1}: ${errorMessage}`);
        }
      }

      // Complete session if at least one photo was uploaded (do this before preparing response)
      if (uploadedPhotos.length > 0) {
        try {
          await this.sessionService.completeSession(sessionId, {});
        } catch (error) {
          const errorMessage =
            error instanceof Error ? error.message : 'Unknown error';
          this.logger.error(`Failed to complete session: ${errorMessage}`);
          errors.push(`Failed to complete session: ${errorMessage}`);
        }
      }

      // Prepare response - ensure it's a plain object
      const response = {
        success: true,
        message: `Successfully uploaded ${uploadedPhotos.length} image(s) and completed session`,
        uploaded: uploadedPhotos.length,
        failed: errors.length,
        sessionId: String(sessionId),
        ...(errors.length > 0 && { errors: errors.map(String) }),
      };

      this.logger.log(
        `Returning response for session ${sessionId}: ${JSON.stringify(response)}`,
      );

      // Emit websocket after preparing response but before returning (non-blocking)
      if (uploadedPhotos.length > 0 && session.userId) {
        const userId = session.userId;
        try {
          this.photoboothGateway.emitStopSession(userId);
        } catch (wsError) {
          this.logger.warn(
            `Failed to emit websocket stop_session: ${wsError instanceof Error ? wsError.message : 'Unknown error'}`,
          );
        }
      }

      // Return response immediately
      return response;
    } catch (error) {
      this.logger.error(
        `Error in uploadMultipleImages: ${error instanceof Error ? error.message : 'Unknown error'}`,
      );
      throw error;
    }
  }

  @Post('upload-image')
  @UseInterceptors(FileInterceptor('file'))
  @ApiOperation({ summary: 'Upload image file and get URL' })
  @ApiConsumes('multipart/form-data')
  @ApiBody({
    schema: {
      type: 'object',
      properties: {
        file: {
          type: 'string',
          format: 'binary',
          description: 'Image file to upload',
        },
      },
      required: ['file'],
    },
  })
  @ApiResponse({
    status: 201,
    description: 'Image uploaded successfully',
    schema: {
      type: 'object',
      properties: {
        imageUrl: {
          type: 'string',
          description: 'URL of the uploaded image',
          example: 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD...',
        },
        publicId: {
          type: 'string',
          description: 'Public ID for the uploaded image',
          example: 'photobooth/upload/1234567890',
        },
      },
    },
  })
  @ApiResponse({ status: 400, description: 'Bad request - Invalid file' })
  async uploadImage(
    @UploadedFile(
      new ParseFilePipe({
        validators: [
          new MaxFileSizeValidator({ maxSize: 10 * 1024 * 1024 }), // 10MB
          new FileTypeValidator({
            fileType: /^image\/(jpeg|jpg|png|gif|webp)$/,
          }),
        ],
      }),
    )
    file: Express.Multer.File,
  ) {
    // Upload to Cloudinary
    const folder = 'photoboth/uploads';
    const imageUrl = await this.cloudinaryService.uploadImage(file, folder);
    const publicId = this.cloudinaryService.extractPublicId(imageUrl);

    return {
      imageUrl,
      publicId,
    };
  }

  @Get('photos/:id')
  @ApiOperation({ summary: 'Get photo by ID' })
  @ApiParam({
    name: 'id',
    description: 'Photo ID (UUID)',
    example: '123e4567-e89b-12d3-a456-426614174000',
  })
  @ApiResponse({
    status: 200,
    description: 'Photo retrieved successfully',
    type: PhotoResponseDto,
  })
  @ApiResponse({ status: 404, description: 'Photo not found' })
  async getPhoto(@Param('id') id: string) {
    return this.photoService.findOne(id);
  }
}
