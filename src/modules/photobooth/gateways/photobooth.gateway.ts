import {
  WebSocketGateway,
  WebSocketServer,
  OnGatewayConnection,
  OnGatewayDisconnect,
} from '@nestjs/websockets';
import { Server, Socket } from 'socket.io';
import { Logger } from '@nestjs/common';

@WebSocketGateway({
  cors: {
    origin: '*',
    credentials: true,
  },
  namespace: '/photobooth',
  transports: ['polling'], // Chỉ sử dụng HTTP long polling
  allowEIO3: true, // Cho phép Socket.IO v3 clients
})
export class PhotoboothGateway
  implements OnGatewayConnection, OnGatewayDisconnect
{
  @WebSocketServer()
  server: Server;

  private readonly logger = new Logger(PhotoboothGateway.name);

  handleConnection(client: Socket) {
    this.logger.log(`Client connected: ${client.id}`);
  }

  handleDisconnect(client: Socket) {
    this.logger.log(`Client disconnected: ${client.id}`);
  }

  emitStartSession(userId: string, sessionId: string) {
    this.logger.log(`Emitting start_session for user: ${userId}, session: ${sessionId}`);
    this.server.emit('start_session', {
      type: 'start_session',
      data: { user_id: userId, session_id: sessionId },
    });
  }

  emitStopSession(userId: string) {
    this.logger.log(`Emitting stop_session for user: ${userId}`);
    this.server.emit('stop_session', {
      type: 'stop_session',
      data: { user_id: userId },
    });
  }

  emitStartCapture(sessionId: string) {
    this.logger.log(`Emitting start_capture for session: ${sessionId}`);
    this.server.emit('start_capture', {
      type: 'start_capture',
      data: { session_id: sessionId },
    });
  }

  emitChangeFilter(filterId: string) {
    this.logger.log(`Emitting change_filter for filter: ${filterId}`);
    this.server.emit('change_filter', {
      type: 'change_filter',
      data: { filter_id: filterId },
    });
  }

  emitAddFilter(filterId: string) {
    this.logger.log(`Emitting add_filter for filter: ${filterId}`);
    this.server.emit('add_filter', {
      type: 'add_filter',
      data: { filter_id: filterId },
    });
  }

  emitDeleteFilter(filterId: string) {
    this.logger.log(`Emitting delete_filter for filter: ${filterId}`);
    this.server.emit('delete_filter', {
      type: 'delete_filter',
      data: { filter_id: filterId },
    });
  }
}
