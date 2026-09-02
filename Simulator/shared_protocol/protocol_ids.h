/* GENERATED FROM SharedProtocol/spec.json — DO NOT EDIT. Run: python SharedProtocol/generate.py */

#ifndef PROTOCOL_IDS_H
#define PROTOCOL_IDS_H

#define REQ_GET_INFO       0x01
#define REQ_RUN_STAGE      0x02
#define REQ_LIST_FILES     0x03
#define REQ_READ_FILE      0x04

#define RES_OK             0x81
#define RES_FAIL           0x82
#define RES_CRASH          0x83
#define RES_FILE_ERROR     0x84
#define RES_PROTOCOL_ERROR 0x85

#define FRAME_TYPE_SIZE_BYTES   1
#define FRAME_LENGTH_SIZE_BYTES 4

#endif /* PROTOCOL_IDS_H */
